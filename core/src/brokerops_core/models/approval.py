from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field

from brokerops_core.models.sensitivity import RESTRICTED_CONTENT


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    """The HITL spine — every human gate in any workflow passes through one of these."""

    # Stamped by the scoped data layer (BOP-006).
    tenant_id: str = ""
    id: str
    workflow: str
    graph_thread_id: str
    kind: str
    # The gate's open, kind-specific detail — for a drafted-comms gate it carries
    # the recipient/subject/body of the drafted message. Role-restricted at egress
    # (BOP-027): redacted (→ null) when a viewer-open surface (the transaction hub)
    # reads it, so a viewer sees a gate exists (kind/status) but not its draft
    # content. Marked here; the redaction fires only where a route scrubs by role.
    payload: Annotated[dict[str, Any] | None, RESTRICTED_CONTENT]
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalDecision(BaseModel):
    decision: ApprovalStatus = Field(description="approved or rejected")
    decided_by: str
    edited_payload: dict[str, Any] | None = None
