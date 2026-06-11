import os
from functools import lru_cache
from typing import cast

from fastapi import Request

from brokerops_api.db import ApprovalRepo, TransactionStoreAdmin
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.listing_service import ListingService
from brokerops_followupboss.adapter import FUB_API_BASE, FUBCRMAdapter
from brokerops_mls_reso.adapter import ResoMLSAdapter


def reso_base_url() -> str:
    return os.environ.get("RESO_BASE_URL", "http://localhost:8001")


def build_crm_adapter() -> FUBCRMAdapter:
    return FUBCRMAdapter(
        api_key=os.environ.get("FUB_API_KEY", ""),
        base_url=os.environ.get("FUB_BASE_URL", FUB_API_BASE),
    )


@lru_cache(maxsize=1)
def get_listing_service() -> ListingService:
    return ListingService(ResoMLSAdapter(base_url=reso_base_url()))


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
