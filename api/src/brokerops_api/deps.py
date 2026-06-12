import os
from functools import lru_cache
from typing import cast

import httpx
from fastapi import FastAPI, Request

from brokerops_api.db import ApprovalRepo, TransactionStoreAdmin
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.listing_service import ListingService
from brokerops_followupboss.adapter import FUB_API_BASE, FUBCRMAdapter
from brokerops_mls_reso.adapter import ResoMLSAdapter
from brokerops_vapi.adapter import VAPI_API_BASE, VapiVoiceAdapter

# Sentinel base-URL value: run the integration's stub in-process over an ASGI
# transport — no separate service, no credentials. This is how a demo client
# deploys to Cloud Run as a single container with zero secrets.
INTERNAL = "internal"


def _internal_client(app: FastAPI, **client_kwargs: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://stub.internal",
        **client_kwargs,  # type: ignore[arg-type]
    )


def reso_base_url() -> str:
    return os.environ.get("RESO_BASE_URL", "http://localhost:8001")


def build_mls_adapter() -> ResoMLSAdapter:
    base_url = reso_base_url()
    if base_url == INTERNAL:
        from brokerops_mls_reso.server import create_app

        return ResoMLSAdapter(
            base_url="http://stub.internal", client=_internal_client(create_app())
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
    return VapiVoiceAdapter(api_key=api_key, base_url=base_url)


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
