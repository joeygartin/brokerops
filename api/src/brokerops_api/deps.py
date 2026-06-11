import os
from functools import lru_cache
from typing import cast

from fastapi import Request

from brokerops_api.db import ApprovalRepo
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.services.listing_service import ListingService
from brokerops_mls_reso.adapter import ResoMLSAdapter


def reso_base_url() -> str:
    return os.environ.get("RESO_BASE_URL", "http://localhost:8001")


@lru_cache(maxsize=1)
def get_listing_service() -> ListingService:
    return ListingService(ResoMLSAdapter(base_url=reso_base_url()))


def get_workflow_engine(request: Request) -> WorkflowEngine:
    return cast(WorkflowEngine, request.app.state.workflow_engine)


def get_approval_repo(request: Request) -> ApprovalRepo:
    return cast(ApprovalRepo, request.app.state.approval_repo)
