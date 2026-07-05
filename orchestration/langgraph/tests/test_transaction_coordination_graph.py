from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from conftest import FakeTransactionStore, GraphFakeCRM, make_message_service
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brokerops_core.models.message import MessageStatus
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.services.message_send import MessageSendService
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

# The same transaction with a reachable external party: the due-soon path's
# drafted reminder (BOP-019) only fires when the milestone owner is a party
# with an email on file.
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


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _build(
    store: FakeTransactionStore,
    crm: GraphFakeCRM | None = None,
    messages: MessageSendService | None = None,
) -> Any:
    return build_transaction_coordination(
        store, crm or GraphFakeCRM(), messages or make_message_service()[0], InMemorySaver()
    )


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


async def test_due_soon_with_reachable_party_drafts_reminder_email_gate() -> None:
    crm = GraphFakeCRM()
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2), _milestone("M-2", 30)])
    graph = _build(store, crm, messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke({"transaction_id": "TXN-1001"}, config)
    # CRM reminder task behavior is unchanged — the drafted email is additive
    assert len(paused["reminder_task_ids"]) == 1
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "approve_outbound_message"
    assert payload["channel"] == "email"
    assert payload["recipient"] == "tc@example.test"
    assert "Milestone M-1" in payload["subject"]
    assert payload["transaction_id"] == "TXN-1001"
    # the draft row is persisted and pending — nothing has been sent
    row = message_store.rows[payload["message_id"]]
    assert row.status is MessageStatus.PENDING_APPROVAL
    assert email.sent == []

    result = await graph.ainvoke(
        Command(
            resume={
                "decision": "approved",
                "decided_by": "tc-lead",
                "edited_payload": {"body": "Edited reminder body."},
            }
        ),
        config,
    )
    assert result["outcome"] == "reminder_email_sent"
    # the edited text is exactly what shipped, and the row records the send
    assert [m.body for m in email.sent] == ["Edited reminder body."]
    sent_row = message_store.rows[payload["message_id"]]
    assert sent_row.status is MessageStatus.SENT
    assert sent_row.body == "Edited reminder body."
    assert sent_row.recipient == "tc@example.test"


async def test_rejected_reminder_email_sends_nothing() -> None:
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2)])
    graph = _build(store, None, messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke({"transaction_id": "TXN-1001"}, config)
    payload = paused["__interrupt__"][0].value
    result = await graph.ainvoke(
        Command(resume={"decision": "rejected", "decided_by": "tc-lead"}), config
    )
    assert result["outcome"] == "reminder_email_dismissed"
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.REJECTED


async def test_blank_edited_body_fails_loudly_instead_of_reverting() -> None:
    # F4: a present-but-blank edited body must never silently fall back to the
    # original draft — the resume fails loudly and nothing is sent.
    import pytest

    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2)])
    graph = _build(store, None, messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke({"transaction_id": "TXN-1001"}, config)
    payload = paused["__interrupt__"][0].value
    with pytest.raises(ValueError, match="blank"):
        await graph.ainvoke(
            Command(
                resume={
                    "decision": "approved",
                    "decided_by": "tc-lead",
                    "edited_payload": {"body": "   "},
                }
            ),
            config,
        )
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.PENDING_APPROVAL


async def test_cron_suppression_flag_skips_only_the_drafted_tail() -> None:
    # F1: with suppress_reminder_email set (cron saw a pending outbound gate),
    # the run still sends CRM reminders and completes — no second email gate.
    crm = GraphFakeCRM()
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN_WITH_PARTY], [_milestone("M-1", 2)])
    result = await _build(store, crm, messages).ainvoke(
        {"transaction_id": "TXN-1001", "suppress_reminder_email": True}, _config(uuid4().hex)
    )
    assert result["outcome"] == "reminders_sent"
    assert "__interrupt__" not in result
    assert len(crm.created_tasks) == 1
    assert message_store.rows == {} and email.sent == []


async def test_due_soon_without_reachable_party_skips_the_drafted_tail() -> None:
    # Owner "TC Team" is not a transaction party → no recipient → the run ends
    # exactly as before BOP-019 (CRM tasks only, no gate, no message row).
    messages, email, message_store = make_message_service()
    store = FakeTransactionStore([TXN], [_milestone("M-1", 2)])
    result = await _build(store, None, messages).ainvoke(
        {"transaction_id": "TXN-1001"}, _config(uuid4().hex)
    )
    assert result["outcome"] == "reminders_sent"
    assert "__interrupt__" not in result
    assert message_store.rows == {} and email.sent == []


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
