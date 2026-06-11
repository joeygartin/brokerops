from datetime import date, timedelta

from brokerops_core.models.milestone import Milestone, MilestoneStatus, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.milestone_engine import (
    MilestoneClass,
    assess_milestone,
    assess_milestones,
    draft_escalation_note,
    draft_milestone_reminder,
    worst_classification,
)

TODAY = date(2026, 6, 11)


def _milestone(days_out: int, **overrides: object) -> Milestone:
    base: dict[str, object] = {
        "id": f"M-{days_out}",
        "transaction_id": "TXN-1001",
        "type": MilestoneType.INSPECTION,
        "title": "Home inspection",
        "due_date": TODAY + timedelta(days=days_out),
    }
    base.update(overrides)
    return Milestone.model_validate(base)


def _txn() -> Transaction:
    return Transaction(
        id="TXN-1001",
        listing_key="RM1004",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=TODAY - timedelta(days=10),
        close_date=TODAY + timedelta(days=20),
    )


def test_classification_boundaries() -> None:
    assert assess_milestone(_milestone(-1), TODAY).classification is MilestoneClass.OVERDUE
    assert assess_milestone(_milestone(0), TODAY).classification is MilestoneClass.DUE_SOON
    assert assess_milestone(_milestone(3), TODAY).classification is MilestoneClass.DUE_SOON
    assert assess_milestone(_milestone(4), TODAY).classification is MilestoneClass.ON_TRACK


def test_blocked_reason_overrides_dates() -> None:
    blocked = _milestone(-5, blocked_reason="Awaiting lender underwriting")
    assert assess_milestone(blocked, TODAY).classification is MilestoneClass.BLOCKED_EXTERNAL


def test_non_pending_milestones_are_not_assessed() -> None:
    milestones = [
        _milestone(-2, id="M-done", status=MilestoneStatus.COMPLETE),
        _milestone(10, id="M-open"),
    ]
    assessments = assess_milestones(milestones, TODAY)
    assert [a.milestone_id for a in assessments] == ["M-open"]


def test_worst_classification_severity_order() -> None:
    mixed = assess_milestones(
        [_milestone(-1, id="M-a"), _milestone(2, id="M-b"), _milestone(30, id="M-c")], TODAY
    )
    assert worst_classification(mixed) is MilestoneClass.OVERDUE
    assert worst_classification(assess_milestones([_milestone(30)], TODAY)) is (
        MilestoneClass.ON_TRACK
    )
    assert worst_classification([]) is MilestoneClass.ON_TRACK


def test_drafts_mention_transaction_and_deadline() -> None:
    reminder = draft_milestone_reminder(_txn(), _milestone(2), 2)
    assert "TXN-1001" in reminder and "RM1004" in reminder
    escalation = draft_escalation_note(_txn(), _milestone(-4), 4)
    assert "OVERDUE" in escalation and "4 day(s) overdue" in escalation
