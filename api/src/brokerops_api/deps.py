import logging
import os
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, cast

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request

if TYPE_CHECKING:
    from brokerops_api.auth.session import SessionRefresher
    from brokerops_email_ses.adapter import SESEmailAdapter
    from brokerops_sierra_crm.adapter import SierraCRMAdapter

from brokerops_api.db import ApprovalRepo, TransactionStoreAdmin
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.ports.audit import TransactionAuditLog
from brokerops_core.ports.auth import MagicTokenStore
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.documents import DocumentStore
from brokerops_core.ports.drafting import DraftingPort
from brokerops_core.ports.email import EmailSender
from brokerops_core.ports.extraction import ExtractionPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.files import FilesPort
from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal, Role
from brokerops_core.ports.messaging import EmailPort, MessageStore, SMSPort
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.drafting import DeterministicDrafter
from brokerops_core.services.egress import EGRESS_FILTERED_MARKER
from brokerops_core.services.email import ConsoleEmailSender
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.identity import DemoIdentityVerifier, EmailAllowlist, RoleResolver
from brokerops_core.services.listing_service import ListingService
from brokerops_core.services.magic_link import MagicLinkService
from brokerops_core.services.message_send import MessageSendService
from brokerops_email_stub.adapter import StubEmailAdapter
from brokerops_followupboss.adapter import FUB_API_BASE, FUBCRMAdapter
from brokerops_twilio_sms.adapter import TWILIO_API_BASE, TwilioSMSAdapter
from brokerops_mls_reso.adapter import ResoMLSAdapter
from brokerops_vapi.adapter import VAPI_API_BASE, VapiVoiceAdapter

logger = logging.getLogger(__name__)

# Sentinel base-URL value: run the integration's stub in-process over an ASGI
# transport — no separate service, no credentials. This is how a demo client
# deploys to Cloud Run as a single container with zero secrets.
INTERNAL = "internal"


def _internal_client(
    app: FastAPI, base_url: str = "http://stub.internal", **client_kwargs: object
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base_url,
        **client_kwargs,  # type: ignore[arg-type]
    )


def reso_base_url() -> str:
    return os.environ.get("RESO_BASE_URL", "http://localhost:8001/odata")


def demo_routes_enabled() -> bool:
    """Whether the demo seed/reset routes are mounted (default OFF).

    `/demo/seed` with ``{"reset": true}`` drops the tenant's transactions and
    milestones, so it must never be reachable on a real client deploy. main.py mounts the
    demo router only when this is true — so a client deploy has no such route at all. It is
    enabled only for the bundled demo: ``docker compose up`` sets ENABLE_DEMO_ROUTES=true
    and the GCP demo sets ``enable_demo_routes=true``.
    """
    return os.environ.get("ENABLE_DEMO_ROUTES", "false").strip().lower() in {"1", "true", "yes"}


def deploy_tenant() -> str:
    """This deploy's tenant id (BOP-006).

    brokerops is single-tenant per deploy, so the tenant is a deploy constant from
    config — never model-supplied. ``TenantScopeMiddleware`` binds it around every
    request so the data layer can read it from the request boundary; default "demo"
    keeps the zero-credential demo working.
    """
    return os.environ.get("TENANT_ID", "demo")


def build_mls_adapter() -> ResoMLSAdapter:
    base_url = reso_base_url()
    if base_url == INTERNAL:
        from brokerops_mls_reso.server import create_app

        return ResoMLSAdapter(
            base_url="http://stub.internal/odata",
            client=_internal_client(create_app(), base_url="http://stub.internal/odata"),
        )
    return ResoMLSAdapter(base_url=base_url, auth_token=os.environ.get("RESO_AUTH_TOKEN") or None)


CRM_VENDORS = ("followupboss", "sierra")


def crm_vendor() -> str:
    """The CRM this deploy is wired to — a closed, explicit selector (ADR-0016,
    mirroring the ORCHESTRATOR / EXTRACTION_BACKEND posture). Unset keeps the
    FollowUpBoss default so the zero-credential demo is unchanged; an unknown
    value is a startup error, never a silent fallback."""
    vendor = os.environ.get("CRM_VENDOR", "").strip().lower()
    if not vendor:
        return "followupboss"
    if vendor not in CRM_VENDORS:
        raise RuntimeError(f"unknown CRM_VENDOR {vendor!r}; expected one of {sorted(CRM_VENDORS)}")
    return vendor


def _require_env(name: str, vendor: str) -> str:
    # Fail loud, never downgrade (ADR-0014 posture): an explicitly selected CRM
    # with missing config is a deploy misconfiguration, not a reason to run a
    # different CRM. "unset" is the Terraform placeholder for a secret that was
    # never pushed — same misconfiguration.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(f"CRM_VENDOR={vendor!r} requires {name} to be set to a real value")
    return value


def _build_fub_adapter() -> FUBCRMAdapter:
    api_key = os.environ.get("FUB_API_KEY", "")
    base_url = os.environ.get("FUB_BASE_URL", FUB_API_BASE)
    if base_url == INTERNAL:
        from brokerops_followupboss.stub import create_stub_app

        return FUBCRMAdapter(
            api_key=api_key,
            base_url="http://stub.internal",
            client=_internal_client(create_stub_app(), auth=(api_key, "")),
        )
    return FUBCRMAdapter(api_key=api_key, base_url=base_url)


def _build_sierra_adapter() -> "SierraCRMAdapter":
    from brokerops_sierra_crm.adapter import SIERRA_API_BASE, SierraCRMAdapter
    from brokerops_sierra_crm.stub import STUB_TASK_ANCHOR_LEAD_ID, STUB_TASK_ASSIGNEE_ID

    base_url = os.environ.get("SIERRA_BASE_URL", SIERRA_API_BASE)
    if base_url == INTERNAL:
        from brokerops_sierra_crm.stub import create_stub_app

        return SierraCRMAdapter(
            api_key=os.environ.get("SIERRA_API_KEY", ""),
            base_url="http://stub.internal",
            client=_internal_client(create_stub_app(), headers={"Sierra-ApiKey": "internal-demo"}),
            task_assignee_id=int(
                os.environ.get("SIERRA_TASK_ASSIGNEE_ID", str(STUB_TASK_ASSIGNEE_ID))
            ),
            task_anchor_lead_id=os.environ.get(
                "SIERRA_TASK_ANCHOR_LEAD_ID", STUB_TASK_ANCHOR_LEAD_ID
            ),
        )
    # Against the real API everything must be explicit: the key, who Sierra
    # tasks are assigned to, and which lead anchors contact-less tasks.
    return SierraCRMAdapter(
        api_key=_require_env("SIERRA_API_KEY", "sierra"),
        base_url=base_url,
        task_assignee_id=int(_require_env("SIERRA_TASK_ASSIGNEE_ID", "sierra")),
        task_anchor_lead_id=_require_env("SIERRA_TASK_ANCHOR_LEAD_ID", "sierra"),
    )


def build_crm_adapter() -> CRMPort:
    vendor = crm_vendor()
    if vendor == "sierra":
        return _build_sierra_adapter()
    return _build_fub_adapter()


def build_voice_adapter() -> VapiVoiceAdapter:
    api_key = os.environ.get("VAPI_API_KEY", "")
    base_url = os.environ.get("VAPI_BASE_URL", VAPI_API_BASE)
    if base_url == INTERNAL:
        from brokerops_vapi.stub import create_stub_app

        return VapiVoiceAdapter(
            api_key=api_key,
            base_url="http://stub.internal",
            client=_internal_client(create_stub_app()),
        )
    return VapiVoiceAdapter(
        api_key=api_key,
        base_url=base_url,
        phone_number_id=os.environ.get("VAPI_PHONE_NUMBER_ID") or None,
    )


EMAIL_PROVIDERS = ("stub", "ses", "sendgrid")


def _require_ses_env(name: str) -> str:
    # Fail loud, never downgrade (ADR-0014/0015): EMAIL_PROVIDER=ses with
    # missing SES config is a deploy misconfiguration, not a reason to run the
    # stub. "unset" is the Terraform placeholder for a secret that was never
    # pushed — same misconfiguration.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(f"EMAIL_PROVIDER='ses' requires {name} to be set to a real value")
    return value


def _build_ses_email_adapter() -> "SESEmailAdapter":
    from brokerops_email_ses.adapter import DEFAULT_REGION, SESEmailAdapter

    # Credentials come from Secret Manager (scripts/setup_ses.sh pushes the
    # secret access key; the id and from-address are non-secret deploy vars).
    return SESEmailAdapter(
        access_key_id=_require_ses_env("SES_ACCESS_KEY_ID"),
        secret_access_key=_require_ses_env("SES_SECRET_ACCESS_KEY"),
        from_address=_require_ses_env("SES_FROM_ADDRESS"),
        region=os.environ.get("SES_REGION", DEFAULT_REGION),
        # SES_BASE_URL points the adapter at an SES-shaped stub (tests/tooling);
        # unset → the region's real endpoint.
        base_url=os.environ.get("SES_BASE_URL") or None,
    )


def build_email_port() -> EmailPort:
    """The outbound business-email provider (EmailPort, ADR-0015) — NOT the
    magic-link EmailSender (ADR-0008), which keeps its own SMTP config.

    Closed, explicit selector in the ADR-0014 posture: EMAIL_PROVIDER names the
    provider; nothing is inferred from key presence. Unset → the stub, so demo
    mode stays zero-credential; an unknown value raises at wiring; `ses`
    (BOP-016) and `sendgrid` (BOP-017) each require their real credentials and
    sender identity and fail loud without them — never a silent downgrade to
    the stub.
    """
    provider = os.environ.get("EMAIL_PROVIDER", "").strip().lower() or "stub"
    if provider == "stub":
        # The stub's default base URL is the `internal` sentinel (unlike real
        # integrations, there is no external counterpart to default to), so
        # `docker compose up` exercises the email flow with zero configuration.
        # `or` (not a get() default): compose interpolates unset vars to the
        # empty string, which must mean internal too, not base_url="".
        base_url = os.environ.get("EMAIL_BASE_URL") or INTERNAL
        if base_url == INTERNAL:
            from brokerops_email_stub.stub import create_stub_app

            return StubEmailAdapter(
                base_url="http://stub.internal", client=_internal_client(create_stub_app())
            )
        return StubEmailAdapter(base_url=base_url)
    if provider == "ses":
        return _build_ses_email_adapter()
    if provider == "sendgrid":
        from brokerops_email_sendgrid.adapter import SENDGRID_API_BASE, SendGridEmailAdapter

        # Explicitly selected provider, explicit config: the API key and the
        # domain-authenticated sender identity must both be real values —
        # missing or placeholder ("unset", the Terraform push-was-forgotten
        # value) fails loud at wiring, never a silent downgrade to the stub.
        config = {
            name: os.environ.get(name, "") for name in ("SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL")
        }
        for name, value in config.items():
            if not value or value == "unset":
                raise RuntimeError(
                    f"EMAIL_PROVIDER='sendgrid' requires {name} to be set to a real value"
                )
        return SendGridEmailAdapter(
            api_key=config["SENDGRID_API_KEY"],
            from_email=config["SENDGRID_FROM_EMAIL"],
            base_url=os.environ.get("SENDGRID_BASE_URL") or SENDGRID_API_BASE,
        )
    raise RuntimeError(
        f"unknown EMAIL_PROVIDER {provider!r}; expected one of {sorted(EMAIL_PROVIDERS)}"
    )


SMS_PROVIDERS = ("stub", "twilio")

# Twilio test-credential magic from-number (always "sends" successfully) — the
# stub's default sender, so demo config needs no phone number anywhere.
_STUB_FROM_NUMBER = "+15005550006"


def build_sms_port() -> SMSPort:
    """The outbound SMS provider (SMSPort, BOP-018) — EMAIL_PROVIDER's sibling.

    Closed, explicit selector in the ADR-0014 posture: SMS_PROVIDER names the
    provider; nothing is inferred from key presence. Unset → the bundled
    recorded-shape Twilio stub, in-process over the `internal` sentinel, so demo
    mode stays zero-credential; `twilio` requires the account SID, auth token,
    and a sender (from-number or messaging-service SID) and fails loud without
    them — never a silent downgrade to the stub; an unknown value raises.
    """
    provider = os.environ.get("SMS_PROVIDER", "").strip().lower() or "stub"
    if provider == "stub":
        # Like the email stub, the default base URL is the `internal` sentinel —
        # there is no external stub service to default to — so `docker compose up`
        # exercises the SMS flow with zero configuration.
        base_url = os.environ.get("SMS_BASE_URL", INTERNAL)
        if base_url == INTERNAL:
            from brokerops_twilio_sms.stub import create_stub_app

            return TwilioSMSAdapter(
                account_sid="ACstub00000000000000000000000000",
                auth_token="stub-token",
                from_number=_STUB_FROM_NUMBER,
                base_url="http://stub.internal",
                client=_internal_client(create_stub_app()),
            )
        return TwilioSMSAdapter(
            account_sid="ACstub00000000000000000000000000",
            auth_token="stub-token",
            from_number=_STUB_FROM_NUMBER,
            base_url=base_url,
        )
    if provider == "twilio":
        # Fail loud, never downgrade (the _require_env posture): an explicitly
        # selected real provider with missing config is a deploy misconfiguration.
        # "unset" is the Terraform placeholder for a secret that was never pushed.
        def _require(name: str) -> str:
            value = os.environ.get(name, "")
            if not value or value == "unset":
                raise RuntimeError(
                    f"SMS_PROVIDER='twilio' requires {name} to be set to a real value"
                )
            return value

        account_sid = _require("TWILIO_ACCOUNT_SID")
        auth_token = _require("TWILIO_AUTH_TOKEN")
        from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        messaging_service_sid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")
        if not from_number and not messaging_service_sid:
            raise RuntimeError(
                "SMS_PROVIDER='twilio' requires TWILIO_FROM_NUMBER or "
                "TWILIO_MESSAGING_SERVICE_SID (the A2P 10DLC sender — see "
                "docs/A2P_10DLC_ONBOARDING.md)"
            )
        status_callback_url = os.environ.get("TWILIO_STATUS_CALLBACK_URL", "")
        if not status_callback_url:
            # Documented behavior, but silently surprising in production (BOP-037):
            # no pin means no StatusCallback is requested, so rows stay SENT forever
            # (delivery tracking is off) — and if console-level webhooks fire anyway
            # behind a proxy (Cloud Run), the signature is computed over a URL the
            # app can't reconstruct, so every callback 401s. Diagnosability only;
            # the deploy still starts.
            logger.warning(
                "SMS_PROVIDER='twilio' with TWILIO_STATUS_CALLBACK_URL unset: delivery-status "
                "callbacks are not requested (messages stay SENT; no DELIVERED/FAILED "
                "transitions), and behind a proxy any console-configured webhook will fail "
                "signature validation. Set the pin to the public /webhooks/twilio-sms URL "
                "to enable delivery tracking."
            )
        return TwilioSMSAdapter(
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            messaging_service_sid=messaging_service_sid,
            status_callback_url=status_callback_url,
            base_url=os.environ.get("SMS_BASE_URL", TWILIO_API_BASE),
        )
    raise RuntimeError(
        f"unknown SMS_PROVIDER {provider!r}; expected one of {sorted(SMS_PROVIDERS)}"
    )


DRAFTING_BACKENDS = ("deterministic", "pydantic_ai")


def _require_egress_filtered_channels(channels: tuple[object, ...]) -> None:
    """BOP-020 hard gate: an LLM drafter may be wired only when every channel-port
    seam its output egresses through carries the BOP-012 guard treatment.

    Defense in depth, not the primary control: the outbound payload itself is
    secret-shape-scrubbed in ``MessageSendService`` before every ``port.send`` (the
    real outbound-direction DLP), so LLM copy cannot leave unscrubbed regardless.
    This gate additionally refuses to wire the LLM backend onto a seam that never
    went through ``guard_tool_ports`` — detected by ``EGRESS_FILTERED_MARKER`` on
    the wrapped port — so a generated draft only ever flows through a fully hardened
    (tenant-authorized-in, result-filtered-out) seam. Fail closed: no channels
    supplied, or any one of them unfiltered, refuses. The deterministic backend
    never reaches this."""
    unfiltered = [c for c in channels if not getattr(c, EGRESS_FILTERED_MARKER, False)]
    if not channels or unfiltered:
        raise RuntimeError(
            "DRAFTING_BACKEND='pydantic_ai' requires the outbound channel seam to be "
            "egress-filtered (BOP-012); refusing to wire an LLM drafter onto a send path "
            "that is not egress-filtered"
        )


def build_drafting_port(*egress_channels: object) -> DraftingPort:
    """Outbound-message drafting backend (BOP-019/020) — closed, explicit selector
    in the ADR-0014 posture. Unset → deterministic template rendering, so demo
    mode stays zero-credential; `pydantic_ai` is the LLM backend (BOP-020, one
    backend — no raw-SDK twin) and is hard-gated on egress filtering; an unknown
    value raises at wiring.

    ``egress_channels`` are the channel-port seams a drafted message egresses
    through (main.py passes the egress-guarded email and SMS seams — every channel
    ``send_approved`` can dispatch to). They are required only for the LLM backend,
    whose wiring refuses to start unless each carries the BOP-012 egress filter; the
    deterministic backend ignores them."""
    backend = os.environ.get("DRAFTING_BACKEND", "").strip().lower() or "deterministic"
    if backend == "deterministic":
        return DeterministicDrafter()
    if backend == "pydantic_ai":
        # Refuse before touching the key: an unfiltered send path is a wiring bug,
        # not a credentials problem, and the refusal must not depend on config order.
        _require_egress_filtered_channels(egress_channels)
        from brokerops_pydantic_ai_drafting.adapter import DEFAULT_MODEL, PydanticAIDraftingAdapter

        return PydanticAIDraftingAdapter(
            api_key=_require_llm_api_key(backend, selector="DRAFTING_BACKEND"),
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        )
    raise RuntimeError(
        f"unknown DRAFTING_BACKEND {backend!r}; expected one of {sorted(DRAFTING_BACKENDS)}"
    )


FILES_PROVIDERS = ("stub", "google_drive")

# The action-ledger label for file writes. Both providers are Drive-shaped in
# V1 (the stub is a recorded double of the Drive API), so one label keeps the
# trail queryable; a future Dropbox/OneDrive adapter brings its own.
FILES_INTEGRATION = "google_drive"


def build_files_adapter() -> FilesPort:
    """Office-files backend — closed, explicit selector (the ADR-0014 posture).

    FILES_PROVIDER ∈ {stub, google_drive}; unset → the bundled in-memory Drive
    stub, so demo mode stays zero-credential. `google_drive` without credentials
    fails loud at wiring (never silently downgrades to the stub); an unknown
    value raises (mirrors the ORCHESTRATOR / EXTRACTION_BACKEND guards).
    """
    from brokerops_google_drive.adapter import DRIVE_API_BASE, GoogleDriveFilesAdapter

    provider = os.environ.get("FILES_PROVIDER", "").strip().lower()
    # Anchor folder lookup/creation under one Drive folder so a same-named
    # folder elsewhere in the service account's corpus can't capture files.
    root_folder_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID") or None
    if not provider or provider == "stub":
        base_url = os.environ.get("GOOGLE_DRIVE_BASE_URL", INTERNAL)
        if base_url == INTERNAL:
            from brokerops_google_drive.stub import create_stub_app

            return GoogleDriveFilesAdapter(
                base_url="http://stub.internal",
                client=_internal_client(create_stub_app()),
                root_folder_id=root_folder_id,
            )
        # A separately running stub (compose: all api replicas share one tree,
        # and its webViewLinks resolve from the operator's browser).
        return GoogleDriveFilesAdapter(base_url=base_url, root_folder_id=root_folder_id)
    if provider == "google_drive":
        credentials_file = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "")
        if not credentials_file or credentials_file == "unset":
            raise RuntimeError(
                "FILES_PROVIDER='google_drive' requires GOOGLE_DRIVE_CREDENTIALS_FILE "
                "(a per-client service-account JSON mounted from Secret Manager)"
            )
        return GoogleDriveFilesAdapter(
            credentials_file=credentials_file,
            base_url=os.environ.get("GOOGLE_DRIVE_BASE_URL", DRIVE_API_BASE),
            root_folder_id=root_folder_id,
        )
    raise RuntimeError(
        f"unknown FILES_PROVIDER {provider!r}; expected one of {sorted(FILES_PROVIDERS)}"
    )


EXTRACTION_BACKENDS = ("deterministic", "llm", "pydantic_ai")


def _require_llm_api_key(backend: str, *, selector: str = "EXTRACTION_BACKEND") -> str:
    # Fail loud, never downgrade (ADR-0014): an explicitly selected LLM backend
    # with no usable key is a deploy misconfiguration, not a reason to silently
    # run the deterministic default. "unset" is the Terraform placeholder for a
    # secret that was never pushed — same misconfiguration. Shared by the
    # extraction and drafting selectors (both key off LLM_API_KEY); `selector`
    # names the offending env var in the error.
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key or api_key == "unset":
        raise RuntimeError(f"{selector}={backend!r} requires LLM_API_KEY to be set to a real key")
    return api_key


def build_extraction_port() -> ExtractionPort:
    # Closed, explicit selector (ADR-0014): backend choice is deploy config,
    # never key-presence inference. Unset → the deterministic default, so demo
    # mode stays zero-credential; an explicit LLM backend that is misconfigured
    # raises at startup; an unknown value raises (mirrors the ORCHESTRATOR
    # unknown-value guard).
    backend = os.environ.get("EXTRACTION_BACKEND", "").strip().lower()
    if not backend or backend == "deterministic":
        return DeterministicExtractor()
    if backend == "llm":
        from brokerops_llm_extraction.adapter import DEFAULT_MODEL, ClaudeExtractionAdapter

        return ClaudeExtractionAdapter(
            api_key=_require_llm_api_key(backend),
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        )
    if backend == "pydantic_ai":
        from brokerops_pydantic_ai_extraction.adapter import (
            DEFAULT_MODEL as PYDANTIC_AI_DEFAULT_MODEL,
        )
        from brokerops_pydantic_ai_extraction.adapter import PydanticAIExtractionAdapter

        return PydanticAIExtractionAdapter(
            api_key=_require_llm_api_key(backend),
            model=os.environ.get("LLM_MODEL") or PYDANTIC_AI_DEFAULT_MODEL,
        )
    raise RuntimeError(
        f"unknown EXTRACTION_BACKEND {backend!r}; expected one of {sorted(EXTRACTION_BACKENDS)}"
    )


def _google_client_id() -> str | None:
    # "unset" is the Terraform secret/placeholder — treat it as no client.
    client_id = os.environ.get("GOOGLE_OIDC_CLIENT_ID", "")
    return client_id if client_id and client_id != "unset" else None


def _build_allowlist() -> EmailAllowlist:
    emails = {e.strip() for e in os.environ.get("AUTH_ALLOWED_EMAILS", "").split(",") if e.strip()}
    return EmailAllowlist(
        allowed_domain=os.environ.get("AUTH_ALLOWED_DOMAIN") or None,
        allowed_emails=frozenset(emails) or None,
    )


def _csv_env(name: str) -> frozenset[str] | None:
    values = {e.strip() for e in os.environ.get(name, "").split(",") if e.strip()}
    return frozenset(values) or None


def _build_role_resolver() -> RoleResolver:
    # Parallel to the allowlist: no admin/viewer rule set → unrestricted, so an
    # auth-enabled deploy with no role config behaves as a flat admin list.
    return RoleResolver(
        admin_emails=_csv_env("AUTH_ADMIN_EMAILS"),
        admin_domain=os.environ.get("AUTH_ADMIN_DOMAIN") or None,
        viewer_emails=_csv_env("AUTH_VIEWER_EMAILS"),
        viewer_domain=os.environ.get("AUTH_VIEWER_DOMAIN") or None,
    )


def _session_signing_key() -> str:
    key = os.environ.get("SESSION_SIGNING_KEY", "")
    if not key:
        raise RuntimeError(
            "SESSION_SIGNING_KEY is required when a session-issuing auth method (magic) is on"
        )
    return key


def configured_auth_methods() -> list[str]:
    """Which login methods this deployment offers, in display order.

    Authoritative source is AUTH_METHODS (csv); for back-compat with ADR-0007 a
    bare GOOGLE_OIDC_CLIENT_ID alone still enables google. Google is dropped if
    no valid client id is present, so a misconfig degrades rather than 500s.
    """
    requested = {
        m.strip().lower() for m in os.environ.get("AUTH_METHODS", "").split(",") if m.strip()
    }
    if not requested and _google_client_id():
        requested = {"google"}
    methods: list[str] = []
    if "magic" in requested:
        methods.append("magic")
    if "google" in requested and _google_client_id():
        methods.append("google")
    return methods


def build_identity_verifier() -> IdentityVerifier:
    # Zero-credential default: no methods configured → a single demo operator, so
    # demo mode needs no login (ADR-0007/0008). Otherwise build one verifier per
    # acceptable bearer and try them in turn (session JWT, then Google).
    methods = configured_auth_methods()
    if not methods:
        return DemoIdentityVerifier()
    verifiers: list[IdentityVerifier] = []
    if "magic" in methods:
        from brokerops_api.auth.session import SessionTokenVerifier

        verifiers.append(SessionTokenVerifier(_session_signing_key()))
    if "google" in methods:
        from brokerops_google_oidc.adapter import GoogleOIDCVerifier

        verifiers.append(
            GoogleOIDCVerifier(
                client_id=cast(str, _google_client_id()),
                allowed_domain=os.environ.get("AUTH_ALLOWED_DOMAIN") or None,
                allowed_emails=_csv_env("AUTH_ALLOWED_EMAILS"),
                roles=_build_role_resolver(),
            )
        )
    from brokerops_api.auth.composite import CompositeIdentityVerifier

    return CompositeIdentityVerifier(verifiers)


def build_email_sender() -> EmailSender:
    # SMTP when a host is configured; otherwise the console sender logs the link
    # so magic-link login works in demo/local with no provider.
    host = os.environ.get("SMTP_HOST", "")
    if not host:
        return ConsoleEmailSender()
    from brokerops_email_smtp.adapter import SMTPEmailSender

    return SMTPEmailSender(
        host=host,
        port=int(os.environ.get("SMTP_PORT", "587")),
        from_addr=os.environ.get("SMTP_FROM") or "no-reply@brokerops.app",
        username=os.environ.get("SMTP_USERNAME") or None,
        password=os.environ.get("SMTP_PASSWORD") or None,
        use_starttls=os.environ.get("SMTP_STARTTLS", "true").lower() != "false",
    )


def build_magic_link_service(store: MagicTokenStore) -> MagicLinkService | None:
    if "magic" not in configured_auth_methods():
        return None
    from brokerops_api.auth.session import SessionTokenService

    return MagicLinkService(
        store=store,
        email=build_email_sender(),
        session_issuer=SessionTokenService(_session_signing_key()),
        allowlist=_build_allowlist(),
        roles=_build_role_resolver(),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173"),
    )


def build_session_refresher() -> "SessionRefresher | None":
    """The refresh service, present only when session tokens are issued (magic).

    Refresh applies to the api-issued session JWT; a Google ID token is Google's
    to renew, so this is None unless the magic method is on. Re-uses the same
    allowlist + role resolver as login so refresh re-authorizes on every call.
    """
    if "magic" not in configured_auth_methods():
        return None
    from brokerops_api.auth.session import SessionRefresher, SessionTokenService

    key = _session_signing_key()
    return SessionRefresher(
        signing_key=key,
        allowlist=_build_allowlist(),
        roles=_build_role_resolver(),
        service=SessionTokenService(key),
    )


def get_identity_verifier(request: Request) -> IdentityVerifier:
    return cast(IdentityVerifier, request.app.state.identity_verifier)


def get_session_refresher(request: Request) -> "SessionRefresher":
    refresher = request.app.state.session_refresher
    if refresher is None:
        raise HTTPException(status_code=404, detail="token refresh is not enabled")
    return cast("SessionRefresher", refresher)


def get_magic_link_service(request: Request) -> MagicLinkService:
    service = request.app.state.magic_link_service
    if service is None:
        raise HTTPException(status_code=404, detail="magic-link login is not enabled")
    return cast(MagicLinkService, service)


async def get_current_principal(
    verifier: Annotated[IdentityVerifier, Depends(get_identity_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    # The demo verifier ignores the token, so demo mode resolves a principal
    # with no Authorization header (auto demo user). The Google verifier needs a
    # real bearer; "Bearer " is stripped here so adapters only see the token.
    token = authorization
    if token and token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        return await verifier.verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=403 if exc.forbidden else 401, detail=str(exc)) from exc


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_role(minimum: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: admit the caller only if their role meets `minimum`.

    Authorization runs after authentication — the principal is already resolved by
    get_current_principal, so a failure here is a 403 (known identity, insufficient
    role), never a 401. Gate the privilege-sensitive routes; reads stay open to any
    authenticated operator (viewer and up).
    """

    def _guard(principal: PrincipalDep) -> Principal:
        if not principal.role.allows(minimum):
            raise HTTPException(
                status_code=403,
                detail=f"requires {minimum.value} role (you are {principal.role.value})",
            )
        return principal

    return _guard


@lru_cache(maxsize=1)
def get_listing_service() -> ListingService:
    return ListingService(build_mls_adapter())


def get_workflow_engine(request: Request) -> WorkflowEngine:
    return cast(WorkflowEngine, request.app.state.workflow_engine)


def get_approval_repo(request: Request) -> ApprovalRepo:
    return cast(ApprovalRepo, request.app.state.approval_repo)


def get_crm_port(request: Request) -> CRMPort:
    return cast(CRMPort, request.app.state.crm)


def get_transaction_store(request: Request) -> TransactionStore:
    return cast(TransactionStore, request.app.state.transaction_store)


def get_transaction_store_admin(request: Request) -> TransactionStoreAdmin:
    return cast(TransactionStoreAdmin, request.app.state.transaction_store)


def get_voice_port(request: Request) -> VoicePort:
    return cast(VoicePort, request.app.state.voice)


def get_feedback_store(request: Request) -> FeedbackStore:
    return cast(FeedbackStore, request.app.state.feedback_store)


def get_files_port(request: Request) -> FilesPort:
    return cast(FilesPort, request.app.state.files)


def get_document_store(request: Request) -> DocumentStore:
    return cast(DocumentStore, request.app.state.document_store)


def get_audit_log(request: Request) -> TransactionAuditLog:
    # The API's ledger accessor exposes the full read surface — plain listing plus
    # the per-transaction slice (BOP-027); both production stores (Sql/InMemory)
    # implement it. The narrower `AuditLog` remains the type the recording seam's
    # write-side callers depend on.
    return cast(TransactionAuditLog, request.app.state.audit_log)


def get_message_store(request: Request) -> MessageStore:
    return cast(MessageStore, request.app.state.message_store)


def get_message_service(request: Request) -> MessageSendService:
    return cast(MessageSendService, request.app.state.message_service)
