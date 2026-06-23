from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from conftest import FakeTransactionStore, GraphFakeCRM
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.milestone_schedule import generate_milestones
from brokerops_langgraph.graphs.transaction_coordination import build_transaction_coordination

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


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _build(store: FakeTransactionStore, crm: GraphFakeCRM | None = None) -> Any:
    return build_transaction_coordination(store, crm or GraphFakeCRM(), InMemorySaver())


async def test_all_on_track_just_logs() -> None:
    store = FakeTransactionStore([TXN], [_milestone("M-1", 15), _milestone("M-2", 30)])
    result = await _build(store).ainvoke({"transaction_id": "TXN-1001"}, _config(uuid4().hex))
    assert result["outcome"] == "on_track"
    assert "__interrupt__" not in result


async def test_due_soon_sends_reminder_tasks() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore([TXN], [_milestone("M-1", 2), _milestone("M-2", 30)])
    result = await _build(store, crm).ainvoke({"transaction_id": "TXN-1001"}, _config(uuid4().hex))
    assert result["outcome"] == "reminders_sent"
    assert len(result["reminder_task_ids"]) == 1
    assert "Reminder" in crm.created_tasks[0].name
    assert "__interrupt__" not in result


async def test_overdue_escalates_through_hitl_and_bumps_level() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore(
        [TXN], [_milestone("M-late", -4, escalation_level=1), _milestone("M-ok", 30)]
    )
    graph = _build(store, crm)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke({"transaction_id": "TXN-1001"}, config)
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "approve_escalation"
    assert payload["transaction_id"] == "TXN-1001"
    assert payload["milestones"][0]["days_overdue"] == 4

    result = await graph.ainvoke(
        Command(resume={"decision": "approved", "decided_by": "tc-lead"}), config
    )
    assert result["outcome"] == "escalated"
    assert len(result["escalated_task_ids"]) == 1
    assert crm.created_tasks[0].name.startswith("URGENT")
    assert store.milestones["M-late"].escalation_level == 2


async def test_dismissed_escalation_changes_nothing() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore([TXN], [_milestone("M-late", -1)])
    graph = _build(store, crm)
    config = _config(uuid4().hex)
    await graph.ainvoke({"transaction_id": "TXN-1001"}, config)
    result = await graph.ainvoke(
        Command(resume={"decision": "rejected", "decided_by": "tc-lead"}), config
    )
    assert result["outcome"] == "escalation_dismissed"
    assert crm.created_tasks == []
    assert store.milestones["M-late"].escalation_level == 0


async def test_blocked_external_queues_call_intent() -> None:
    store = FakeTransactionStore(
        [TXN],
        [_milestone("M-fin", 12, type=MilestoneType.FINANCING, blocked_reason="Lender silent")],
    )
    result = await _build(store).ainvoke({"transaction_id": "TXN-1001"}, _config(uuid4().hex))
    assert result["outcome"] == "call_queued"
    assert "Lender silent" in result["planned_calls"][0]


async def test_unknown_transaction_ends_not_found() -> None:
    store = FakeTransactionStore([], [])
    result = await _build(store).ainvoke({"transaction_id": "TXN-9999"}, _config(uuid4().hex))
    assert result["outcome"] == "not_found"


async def test_transaction_opened_via_new_path_is_assessed() -> None:
    # BOP-004 step 4: a transaction created through the domain write path (step 1)
    # with a template-generated timeline (step 2) is assessed unchanged. The
    # generated inspection (contract + 10 = TODAY + 2) lands in the due-soon window.
    txn = Transaction(
        id="TXN-NEW",
        listing_key="RM-NEW",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=TODAY - timedelta(days=8),
        close_date=TODAY + timedelta(days=30),
    )
    store = FakeTransactionStore([], [])
    await store.create_transaction(txn, generate_milestones(txn))
    crm = GraphFakeCRM()
    result = await _build(store, crm).ainvoke({"transaction_id": "TXN-NEW"}, _config(uuid4().hex))
    assert result["outcome"] == "reminders_sent"
    assert len(result["reminder_task_ids"]) == 1
