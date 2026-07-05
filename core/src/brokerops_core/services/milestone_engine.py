"""Milestone assessment — all date math and escalation rules live here.

The transaction_coordination graph only routes on these results; it contains
no deadline rules of its own.
"""

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from brokerops_core.models.document import Document
from brokerops_core.models.drafting import DraftContext
from brokerops_core.models.message_templates import MILESTONE_REMINDER_V1
from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction
from brokerops_core.services.drafting import SENDER_NAME

DUE_SOON_DAYS = 3


class MilestoneClass(StrEnum):
    ON_TRACK = "on_track"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    BLOCKED_EXTERNAL = "blocked_external"


# Routing severity: an overdue milestone outranks everything; a near deadline
# outranks an external blocker (the blocker isn't time-critical by itself).
SEVERITY_ORDER = (
    MilestoneClass.OVERDUE,
    MilestoneClass.DUE_SOON,
    MilestoneClass.BLOCKED_EXTERNAL,
    MilestoneClass.ON_TRACK,
)


class MilestoneAssessment(BaseModel):
    milestone_id: str
    classification: MilestoneClass
    days_until_due: int


def assess_milestone(milestone: Milestone, today: date) -> MilestoneAssessment:
    days = (milestone.due_date - today).days
    if milestone.blocked_reason:
        classification = MilestoneClass.BLOCKED_EXTERNAL
    elif days < 0:
        classification = MilestoneClass.OVERDUE
    elif days <= DUE_SOON_DAYS:
        classification = MilestoneClass.DUE_SOON
    else:
        classification = MilestoneClass.ON_TRACK
    return MilestoneAssessment(
        milestone_id=milestone.id, classification=classification, days_until_due=days
    )


def assess_milestones(milestones: list[Milestone], today: date) -> list[MilestoneAssessment]:
    return [assess_milestone(m, today) for m in milestones if m.status is MilestoneStatus.PENDING]


def worst_classification(assessments: list[MilestoneAssessment]) -> MilestoneClass:
    present = {a.classification for a in assessments}
    for classification in SEVERITY_ORDER:
        if classification in present:
            return classification
    return MilestoneClass.ON_TRACK


def expected_document_satisfied(milestone: Milestone, documents: Sequence[Document]) -> bool | None:
    """Whether the milestone's expected document is attached (BOP-021).

    Read-only report: None when the milestone expects no document; otherwise True
    iff a document of the expected kind is attached to this milestone or at the
    transaction level (a document pinned to a *different* milestone doesn't count).
    No workflow routes on this — it gives transaction_coordination a real artifact
    to report on instead of only dates.
    """
    if milestone.expected_document is None:
        return None
    return any(
        document.kind is milestone.expected_document
        and document.milestone_id in (None, milestone.id)
        for document in documents
    )


def draft_milestone_reminder(txn: Transaction, milestone: Milestone, days_until_due: int) -> str:
    return (
        f"Reminder: {milestone.title} for transaction {txn.id} ({txn.listing_key}) is due "
        f"{milestone.due_date.isoformat()} — {days_until_due} day(s) out. "
        f"Owner: {milestone.owner or 'unassigned'}."
    )


def plan_reminder_email(
    txn: Transaction, due_soon: Sequence[tuple[Milestone, int]]
) -> DraftContext | None:
    """The drafted tail of the due-soon path (BOP-019): one reminder email per run.

    Rule: take the most urgent due-soon milestone whose owner is a transaction
    party with an email on file — the external party we can actually reach.
    None means no such recipient exists; the CRM reminder tasks (already sent)
    remain the whole outcome and the workflow skips the drafted tail.
    """
    for milestone, _days in sorted(due_soon, key=lambda pair: pair[1]):
        party = next(
            (p for p in txn.parties if p.name and p.name == milestone.owner and p.email), None
        )
        if party is None:
            continue
        return DraftContext(
            recipient=party.email,
            template_ref=MILESTONE_REMINDER_V1.ref,
            params={
                "recipient_name": party.name,
                "milestone_title": milestone.title,
                "listing_address": txn.listing_key,
                "due_date": milestone.due_date.isoformat(),
                "sender_name": SENDER_NAME,
            },
            contact_id=party.contact_id or "",
            listing_key=txn.listing_key,
            transaction_id=txn.id,
        )
    return None


def draft_escalation_note(txn: Transaction, milestone: Milestone, days_overdue: int) -> str:
    return (
        f"OVERDUE: {milestone.title} for transaction {txn.id} ({txn.listing_key}) was due "
        f"{milestone.due_date.isoformat()} — {days_overdue} day(s) overdue "
        f"(escalation level {milestone.escalation_level})."
    )
