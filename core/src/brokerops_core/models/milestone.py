from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from brokerops_core.models.document import DocumentKind


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
    # Stamped by the scoped data layer alongside its transaction (BOP-006).
    tenant_id: str = ""
    id: str
    transaction_id: str
    type: MilestoneType
    title: str
    due_date: date
    status: MilestoneStatus = MilestoneStatus.PENDING
    owner: str = ""
    escalation_level: int = 0
    blocked_reason: str | None = None
    # The document kind this milestone expects attached (BOP-021), copied from its
    # template. Read-only signal: the engine *reports* attached-vs-expected; no
    # workflow routes on it.
    expected_document: DocumentKind | None = None


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
    # A template step may declare the document kind that proves it (purchase
    # agreement, disclosures, …) so transaction_coordination has a real artifact
    # to check for, not only a date (BOP-021).
    expected_document: DocumentKind | None = None
