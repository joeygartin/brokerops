"""Scenario parity with the LangGraph transaction_coordination suite, on ADK."""

from datetime import date, timedelta
from typing import Any

from workflow_fixtures import (
    FakeTransactionStore,
    GraphFakeCRM,
    final_state,
    make_engine,
    make_message_service,
)

from brokerops_adk.workflows.transaction_coordination import build_transaction_coordination
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.message import MessageStatus
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.services.message_send import MessageSendService
from brokerops_core.services.milestone_schedule import generate_milestones
from brokerops_core.services.workflow_runs import TRANSACTION_COORDINATION

TODAY = date.today()

TXN = Transaction(
    id="TXN-1001",
    listing_key="RM1004",
    stage=TransactionStage.UNDER_CONTRACT,
    contract_date=TODAY - timedelta(days=10),
    close_date=TODAY + timedelta(days=20),
)

# Same transaction with a reachable external party (drafted reminder tail).
TXN_WITH_PARTY = TXN.model_copy(
    update={
        "parties": [
            TransactionParty(role="buyer", name="Jordan Pike", contact_id="101"),
            TransactionParty(role="escrow", name="TC Team", email="tc@example.test"),
        ]
    }
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


def _decision(
    decision: ApprovalStatus, edited_payload: dict[str, Any] | None = None
) -> ApprovalDecision:
    return ApprovalDecision(decision=decision, decided_by="tc-lead", edited_payload=edited_payload)


def _workflow(
    store: FakeTransactionStore,
    crm: GraphFakeCRM | None = None,
    messages: MessageSendService | None = None,
) -> Any:
    return build_transaction_coordination(
        store, crm or GraphFakeCRM(), messages or make_message_service()[0]
    )


async def test_all_on_track_just_logs() -> None:
    store = FakeTransactionStore([TXN], [_milestone("M-1", 15), _milestone("M-2", 30)])
    engine, _ = make_engine(_workflow(store))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "on_track"
    assert run.approval is None


async def test_due_soon_sends_reminder_tasks() -> None:
    crm = GraphFakeCRM()
    store = FakeTransactionStore([TXN], [_milestone("M-1", 2), _milestone("M-2", 30)])
    engine, _ = make_engine(_workflow(store, crm))
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
    engine, sessions = make_engine(_workflow(store, crm))

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
    engine, _ = make_engine(_workflow(store, crm))
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
    engine, _ = make_engine(_workflow(store))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "call_queued"
    assert "Lender silent" in run.output["planned_calls"][0]


async def test_unknown_transaction_ends_not_found() -> None:
    store = FakeTransactionStore([], [])
    engine, _ = make_engine(_workflow(store))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-9999"})
    assert run.status == "not_found"


async def test_due_soon_with_reachable_party_drafts_reminder_email_gate() -> None:
    crm = GraphFakeCRM()
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2), _milestone("M-2", 30)])
    engine, sessions = make_engine(_workflow(store, crm, messages))

    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    payload = run.approval.payload
    assert payload["kind"] == "approve_outbound_message"
    assert payload["channel"] == "email"
    assert payload["recipient"] == "tc@example.test"
    assert "Milestone M-1" in payload["subject"]
    assert payload["transaction_id"] == "TXN-1001"
    # CRM reminder task behavior is unchanged; the draft is pending, unsent
    assert len(crm.created_tasks) == 1
    assert message_store.rows[payload["message_id"]].status is MessageStatus.PENDING_APPROVAL
    assert email.sent == []

    result = await engine.decide(
        run.approval,
        _decision(ApprovalStatus.APPROVED, edited_payload={"body": "Edited reminder body."}),
    )
    assert result.status == "completed"
    state = await final_state(sessions, TRANSACTION_COORDINATION, run.thread_id)
    assert state["outcome"] == "reminder_email_sent"
    # the edited text is exactly what shipped, and the row records the send
    assert [m.body for m in email.sent] == ["Edited reminder body."]
    sent_row = message_store.rows[payload["message_id"]]
    assert sent_row.status is MessageStatus.SENT
    assert sent_row.body == "Edited reminder body."
    assert sent_row.recipient == "tc@example.test"


async def test_rejected_reminder_email_sends_nothing() -> None:
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2)])
    engine, _ = make_engine(_workflow(store, None, messages))

    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.approval is not None
    payload = run.approval.payload

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "reminder_email_dismissed"
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.REJECTED


async def test_due_soon_without_reachable_party_skips_the_drafted_tail() -> None:
    # Owner "TC Team" is not a transaction party → no recipient → the run ends
    # exactly as before BOP-019 (CRM tasks only, no gate, no message row).
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN], [_milestone("M-1", 2)])
    engine, _ = make_engine(_workflow(store, None, messages))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-1001"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "reminders_sent"
    assert run.approval is None
    assert message_store.rows == {} and email.sent == []


async def test_transaction_opened_via_new_path_is_assessed() -> None:
    # BOP-004 step 4: same proof as the LangGraph suite, on ADK — a transaction
    # opened through the new write path + template timeline is assessed unchanged.
    txn = Transaction(
        id="TXN-NEW",
        listing_key="RM-NEW",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=TODAY - timedelta(days=8),
        close_date=TODAY + timedelta(days=30),
    )
    store = FakeTransactionStore([], [])
    await store.create_transaction(txn, generate_milestones(txn))
    engine, _ = make_engine(_workflow(store))
    run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-NEW"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "reminders_sent"
    assert len(run.output["reminder_task_ids"]) == 1
