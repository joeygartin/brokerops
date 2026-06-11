"""Milestone assessment — all date math and escalation rules live here.

The transaction_coordination graph only routes on these results; it contains
no deadline rules of its own.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction

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


def draft_milestone_reminder(txn: Transaction, milestone: Milestone, days_until_due: int) -> str:
    return (
        f"Reminder: {milestone.title} for transaction {txn.id} ({txn.listing_key}) is due "
        f"{milestone.due_date.isoformat()} — {days_until_due} day(s) out. "
        f"Owner: {milestone.owner or 'unassigned'}."
    )


def draft_escalation_note(txn: Transaction, milestone: Milestone, days_overdue: int) -> str:
    return (
        f"OVERDUE: {milestone.title} for transaction {txn.id} ({txn.listing_key}) was due "
        f"{milestone.due_date.isoformat()} — {days_overdue} day(s) overdue "
        f"(escalation level {milestone.escalation_level})."
    )
