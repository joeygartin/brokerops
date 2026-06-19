import os
from functools import lru_cache
from typing import Annotated, cast

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from brokerops_api.db import ApprovalRepo, TransactionStoreAdmin
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.extraction import ExtractionPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.identity import DemoIdentityVerifier
from brokerops_core.services.listing_service import ListingService
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


def build_identity_verifier() -> IdentityVerifier:
    # Zero-credential default: with no OIDC client configured the dashboard and
    # API run under a single demo operator, so demo mode needs no login
    # (ADR-0007). A client id flips to verifying Google ID tokens. "unset" is
    # the Terraform placeholder — treat it as no client, same as the LLM key.
    client_id = os.environ.get("GOOGLE_OIDC_CLIENT_ID", "")
    if not client_id or client_id == "unset":
        return DemoIdentityVerifier()
    from brokerops_google_oidc.adapter import GoogleOIDCVerifier

    allowed_emails = {
        e.strip() for e in os.environ.get("AUTH_ALLOWED_EMAILS", "").split(",") if e.strip()
    }
    return GoogleOIDCVerifier(
        client_id=client_id,
        allowed_domain=os.environ.get("AUTH_ALLOWED_DOMAIN") or None,
        allowed_emails=frozenset(allowed_emails) or None,
    )


def get_identity_verifier(request: Request) -> IdentityVerifier:
    return cast(IdentityVerifier, request.app.state.identity_verifier)


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
