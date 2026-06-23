from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class MilestoneType(StrEnum):
    INSPECTION = "inspection"
    APPRAISAL = "appraisal"
    FINANCING = "financing"
    CLOSING = "closing"
    CUSTOM = "custom"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    WAIVED = "waived"


class Milestone(BaseModel):
    id: str
    transaction_id: str
    type: MilestoneType
    title: str
    due_date: date
    status: MilestoneStatus = MilestoneStatus.PENDING
    owner: str = ""
    escalation_level: int = 0
    blocked_reason: str | None = None


class DueRule(StrEnum):
    """How a template milestone's due date is derived from the transaction."""

    AFTER_CONTRACT = "after_contract"
    BEFORE_CLOSE = "before_close"


class MilestoneTemplate(BaseModel):
    """One step in a client's escrow timeline.

    Pure data: the due date is computed from the transaction's contract/close
    dates by the schedule service, so a timeline can move to per-client config
    later (BOP-004 onboarding model) without changing the generator.
    """

    type: MilestoneType
    title: str
    owner: str = ""
    rule: DueRule
    days: int = 0  # offset magnitude; the sign/anchor is set by `rule`
