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
