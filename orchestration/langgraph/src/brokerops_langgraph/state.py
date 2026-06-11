"""State schemas for the V1 graphs.

State carries IDs and decisions, not entity blobs — nodes re-fetch entities
through core services so checkpoints stay small and never go stale.
"""

from enum import StrEnum
from typing import Any

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
    fub_task_ids: list[str] = Field(default_factory=list)


class TransactionCoordinationState(BaseModel):
    transaction_id: str
    outcome: str = ""
    worst: str = ""
    assessments: list[dict[str, Any]] = Field(default_factory=list)
    reminders: list[str] = Field(default_factory=list)
    reminder_task_ids: list[str] = Field(default_factory=list)
    escalation_approval: ApprovalOutcome | None = None
    escalated_task_ids: list[str] = Field(default_factory=list)
    planned_calls: list[str] = Field(default_factory=list)
