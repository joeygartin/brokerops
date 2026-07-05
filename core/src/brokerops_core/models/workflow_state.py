"""State schemas for the workflow runs — shared by every orchestrator.

State carries IDs and decisions, not entity blobs — nodes re-fetch entities
through core services so persisted workflow state stays small and never goes
stale. Plain Pydantic by rule: orchestration frameworks (LangGraph, ADK)
consume these schemas; none of them leak in.
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


class VapiFollowupState(BaseModel):
    call_id: str
    listing_key: str = ""
    contact_id: str = ""
    transcript: str = ""
    call_outcome: str = ""
    extracted: dict[str, Any] = Field(default_factory=dict)
    feedback_id: str = ""
    note_id: str = ""
    call_log_id: str = ""
    hot_approval: ApprovalOutcome | None = None
    hot_task_id: str = ""
    # Drafted follow-up tail (BOP-019): the id of the PENDING_APPROVAL Message
    # row — nodes re-fetch the row itself — plus the gate decision and any
    # approver-edited text carried from the gate to the post-decision send.
    followup_message_id: str = ""
    followup_approval: ApprovalOutcome | None = None
    followup_edited_subject: str = ""
    followup_edited_body: str = ""
    outcome: str = ""


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
    # Drafted reminder-email tail on the due-soon path (BOP-019); same shape
    # as the vapi follow-up tail.
    reminder_message_id: str = ""
    reminder_approval: ApprovalOutcome | None = None
    reminder_edited_subject: str = ""
    reminder_edited_body: str = ""
