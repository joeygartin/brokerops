import os
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, cast

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request

if TYPE_CHECKING:
    from brokerops_api.auth.session import SessionRefresher

from brokerops_api.db import ApprovalRepo, TransactionStoreAdmin
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.ports.audit import AuditLog
from brokerops_core.ports.auth import MagicTokenStore
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.email import EmailSender
from brokerops_core.ports.extraction import ExtractionPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal, Role
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.email import ConsoleEmailSender
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.identity import DemoIdentityVerifier, EmailAllowlist, RoleResolver
from brokerops_core.services.listing_service import ListingService
from brokerops_core.services.magic_link import MagicLinkService
from brokerops_followupboss.adapter import FUB_API_BASE, FUBCRMAdapter
from brokerops_mls_reso.adapter import ResoMLSAdapter
from brokerops_vapi.adapter import VAPI_API_BASE, VapiVoiceAdapter

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


def build_crm_adapter() -> FUBCRMAdapter:
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


def build_extraction_port() -> ExtractionPort:
    # Zero-credential default: feedback extraction is keyword/pattern based and
    # demo mode runs with no LLM key. A real key flips to the Claude adapter
    # (ADR-0006). "unset" is the Terraform secret placeholder — treat it as no
    # key so a not-yet-pushed secret can't select the LLM path with a bad key.
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key or api_key == "unset":
        return DeterministicExtractor()
    from brokerops_llm_extraction.adapter import DEFAULT_MODEL, ClaudeExtractionAdapter

    return ClaudeExtractionAdapter(
        api_key=api_key, model=os.environ.get("LLM_MODEL", DEFAULT_MODEL)
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


def get_audit_log(request: Request) -> AuditLog:
    return cast(AuditLog, request.app.state.audit_log)
