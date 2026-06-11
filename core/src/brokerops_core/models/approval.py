from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    """The HITL spine — every human gate in any workflow passes through one of these."""

    id: str
    workflow: str
    graph_thread_id: str
    kind: str
    payload: dict[str, Any]
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalDecision(BaseModel):
    decision: ApprovalStatus = Field(description="approved or rejected")
    decided_by: str
    edited_payload: dict[str, Any] | None = None
