"""Scenario parity with the LangGraph transaction_coordination suite, on ADK."""

from datetime import date, timedelta

from workflow_fixtures import FakeTransactionStore, GraphFakeCRM, final_state, make_engine

from brokerops_adk.workflows.transaction_coordination import build_transaction_coordination
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.workflow_runs import TRANSACTION_COORDINATION

TODAY = date.today()

TXN = Transaction(
    id="TXN-1001",
    listing_key="RM1004",
    stage=TransactionStage.UNDER_CONTRACT,
    contract_date=TODAY - timedelta(days=10),
    close_date=TODAY + timedelta(days=20),
)


def _milestone(milestone_id: str, days_out: int, **overrides: object) -> Milestone:
    base: dict[str, object] = {
        "id": milestone_id,
        "transaction_id": "TXN-1001",
        "type": MilestoneType.INSPECTION,
        "title": f"Milestone {milestone_id}",
        "due_date": TODAY + timedelta(days=days_out),
        "owner": "TC Team",
    }
    base.update(overrides)
    return Milestone.model_validate(base)


def _decision(decision: ApprovalStatus) -> ApprovalDecision:
    return ApprovalDecision(decision=decision, decided_by="tc-lead")


async def test_all_on_track_just_logs() -> None:
    store = FakeTransactionStore([TXN], [_milestone("M-1", 15), _milestone("M-2", 30)])
    engine, _ = make_engine(build_transaction_coordination(store, GraphFakeCRM()))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "on_track"
    assert run.approval is None


async def test_due_soon_sends_reminder_tasks() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore([TXN], [_milestone("M-1", 2), _milestone("M-2", 30)])
    engine, _ = make_engine(build_transaction_coordination(store, crm))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "reminders_sent"
    assert len(run.output["reminder_task_ids"]) == 1
    assert "Reminder" in crm.created_tasks[0].name
    assert run.approval is None


async def test_overdue_escalates_through_hitl_and_bumps_level() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore(
        [TXN], [_milestone("M-late", -4, escalation_level=1), _milestone("M-ok", 30)]
    )
    engine, sessions = make_engine(build_transaction_coordination(store, crm))

    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    payload = run.approval.payload
    assert payload["kind"] == "approve_escalation"
    assert payload["transaction_id"] == "TXN-1001"
    assert payload["milestones"][0]["days_overdue"] == 4

    result = await engine.decide(run.approval, _decision(ApprovalStatus.APPROVED))
    assert result.status == "completed"
    state = await final_state(sessions, TRANSACTION_COORDINATION, run.thread_id)
    assert state["outcome"] == "escalated"
    assert len(state["escalated_task_ids"]) == 1
    assert crm.created_tasks[0].name.startswith("URGENT")
    assert store.milestones["M-late"].escalation_level == 2


async def test_dismissed_escalation_changes_nothing() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore([TXN], [_milestone("M-late", -1)])
    engine, _ = make_engine(build_transaction_coordination(store, crm))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.approval is not None

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "escalation_dismissed"
    assert crm.created_tasks == []
    assert store.milestones["M-late"].escalation_level == 0


async def test_blocked_external_queues_call_intent() -> None:
    store = FakeTransactionStore(
        [TXN],
        [_milestone("M-fin", 12, type=MilestoneType.FINANCING, blocked_reason="Lender silent")],
    )
    engine, _ = make_engine(build_transaction_coordination(store, GraphFakeCRM()))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "call_queued"
    assert "Lender silent" in run.output["planned_calls"][0]


async def test_unknown_transaction_ends_not_found() -> None:
    store = FakeTransactionStore([], [])
    engine, _ = make_engine(build_transaction_coordination(store, GraphFakeCRM()))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-9999"})
    assert run.status == "not_found"
