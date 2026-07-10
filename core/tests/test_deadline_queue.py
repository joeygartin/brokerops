from datetime import date, timedelta

from brokerops_core.models.milestone import Milestone, MilestoneStatus, MilestoneType
from brokerops_core.services.milestone_engine import (
    MilestoneClass,
    build_deadline_queue,
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


def test_queue_keeps_only_attention_worthy_classes() -> None:
    milestones = [
        _milestone(-2, id="M-overdue"),
        _milestone(2, id="M-due-soon"),
        _milestone(-1, id="M-blocked", blocked_reason="Awaiting lender"),
        _milestone(30, id="M-on-track"),
    ]
    queue = build_deadline_queue(milestones, TODAY)
    assert {item.milestone_id for item in queue} == {"M-overdue", "M-due-soon", "M-blocked"}


def test_queue_drops_non_pending_milestones() -> None:
    milestones = [
        _milestone(-2, id="M-done", status=MilestoneStatus.COMPLETE),
        _milestone(-2, id="M-waived", status=MilestoneStatus.WAIVED),
        _milestone(-2, id="M-open"),
    ]
    queue = build_deadline_queue(milestones, TODAY)
    assert [item.milestone_id for item in queue] == ["M-open"]


def test_queue_sorts_most_urgent_first() -> None:
    milestones = [
        _milestone(3, id="M-due-later", transaction_id="TXN-A"),
        _milestone(-1, id="M-slightly-overdue", transaction_id="TXN-B"),
        _milestone(0, id="M-due-today", transaction_id="TXN-C"),
        _milestone(-5, id="M-very-overdue", transaction_id="TXN-D"),
        _milestone(-2, id="M-blocked", transaction_id="TXN-E", blocked_reason="Lender"),
    ]
    order = [item.milestone_id for item in build_deadline_queue(milestones, TODAY)]
    # Overdue band first (most overdue leads), then due-soon (soonest leads),
    # then the external blocker regardless of its own date.
    assert order == [
        "M-very-overdue",
        "M-slightly-overdue",
        "M-due-today",
        "M-due-later",
        "M-blocked",
    ]


def test_queue_spans_transactions_and_carries_context() -> None:
    milestones = [
        _milestone(
            -1, id="M-a", transaction_id="TXN-A", type=MilestoneType.FINANCING, title="Loan"
        ),
        _milestone(-1, id="M-b", transaction_id="TXN-B"),
    ]
    queue = build_deadline_queue(milestones, TODAY)
    a = next(item for item in queue if item.milestone_id == "M-a")
    assert a.transaction_id == "TXN-A"
    assert a.milestone_type is MilestoneType.FINANCING
    assert a.title == "Loan"
    assert a.days_until_due == -1
    assert a.classification is MilestoneClass.OVERDUE


def test_queue_ties_break_deterministically() -> None:
    # Same class and same days_until_due across transactions → stable id order.
    milestones = [
        _milestone(-1, id="M-2", transaction_id="TXN-B"),
        _milestone(-1, id="M-1", transaction_id="TXN-A"),
    ]
    order = [item.transaction_id for item in build_deadline_queue(milestones, TODAY)]
    assert order == ["TXN-A", "TXN-B"]


def test_empty_input_is_empty_queue() -> None:
    assert build_deadline_queue([], TODAY) == []
