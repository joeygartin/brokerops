"""generate_milestones — date math for a transaction's escrow timeline (BOP-004 #2)."""

from datetime import date

import pytest

from brokerops_core.models.milestone import (
    DueRule,
    MilestoneStatus,
    MilestoneTemplate,
    MilestoneType,
)
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.milestone_schedule import (
    DEFAULT_TIMELINE,
    generate_milestones,
)

CONTRACT = date(2026, 6, 1)
CLOSE = date(2026, 7, 1)


def _txn(close_date: date | None = CLOSE) -> Transaction:
    return Transaction(
        id="TXN-2001",
        listing_key="RM-2001",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=CONTRACT,
        close_date=close_date,
    )


def test_after_contract_offset() -> None:
    template = [
        MilestoneTemplate(
            type=MilestoneType.INSPECTION,
            title="Home inspection",
            rule=DueRule.AFTER_CONTRACT,
            days=10,
        )
    ]
    [milestone] = generate_milestones(_txn(), template)
    assert milestone.due_date == date(2026, 6, 11)  # contract + 10
    assert milestone.status is MilestoneStatus.PENDING


def test_before_close_offset() -> None:
    template = [
        MilestoneTemplate(
            type=MilestoneType.CUSTOM,
            title="Final walkthrough",
            rule=DueRule.BEFORE_CLOSE,
            days=1,
        )
    ]
    [milestone] = generate_milestones(_txn(), template)
    assert milestone.due_date == date(2026, 6, 30)  # close - 1


def test_before_close_with_zero_days_lands_on_close() -> None:
    template = [
        MilestoneTemplate(
            type=MilestoneType.CLOSING, title="Closing", rule=DueRule.BEFORE_CLOSE, days=0
        )
    ]
    [milestone] = generate_milestones(_txn(), template)
    assert milestone.due_date == CLOSE


def test_before_close_without_close_date_raises() -> None:
    template = [
        MilestoneTemplate(
            type=MilestoneType.CLOSING, title="Closing", rule=DueRule.BEFORE_CLOSE, days=0
        )
    ]
    with pytest.raises(ValueError, match="no close_date"):
        generate_milestones(_txn(close_date=None), template)


def test_after_contract_does_not_need_close_date() -> None:
    template = [
        MilestoneTemplate(
            type=MilestoneType.INSPECTION,
            title="Home inspection",
            rule=DueRule.AFTER_CONTRACT,
            days=5,
        )
    ]
    [milestone] = generate_milestones(_txn(close_date=None), template)
    assert milestone.due_date == date(2026, 6, 6)


def test_ids_are_deterministic_and_unique() -> None:
    first = generate_milestones(_txn())
    second = generate_milestones(_txn())
    ids = [m.id for m in first]
    assert ids == [m.id for m in second]  # deterministic across regeneration
    assert len(ids) == len(set(ids))  # unique
    assert all(m.transaction_id == "TXN-2001" for m in first)


def test_default_timeline_is_ordered_and_complete() -> None:
    milestones = generate_milestones(_txn())
    assert len(milestones) == len(DEFAULT_TIMELINE)
    due_dates = [m.due_date for m in milestones]
    # Inspection(+10) < appraisal(+17) < financing(+21) < walkthrough(close-1) < closing(close)
    assert due_dates == sorted(due_dates)
    assert milestones[-1].type is MilestoneType.CLOSING
    assert milestones[-1].due_date == CLOSE
