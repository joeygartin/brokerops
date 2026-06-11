"""State schemas for the V1 graphs.

State carries IDs and decisions, not entity blobs — nodes re-fetch entities
through core services so checkpoints stay small and never go stale.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from brokerops_core.models.approval import ApprovalStatus
from brokerops_core.models.marketing import MarketingDraft


class WorkflowStage(StrEnum):
    INTAKE = "intake"
    NOT_FOUND = "not_found"
    NOT_ELIGIBLE = "not_eligible"
    DRAFTED = "drafted"
    PUBLISHED = "published"
    REJECTED = "rejected"


class ApprovalOutcome(BaseModel):
    decision: ApprovalStatus
    decided_by: str


class ListingToContractState(BaseModel):
    listing_key: str
    stage: WorkflowStage = WorkflowStage.INTAKE
    draft: MarketingDraft | None = None
    approval: ApprovalOutcome | None = None
    planned_tasks: list[str] = Field(default_factory=list)
