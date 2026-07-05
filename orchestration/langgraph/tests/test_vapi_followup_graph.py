from typing import Any
from uuid import uuid4

import pytest
from conftest import FakeFeedbackStore, FakeVoice, GraphFakeCRM, make_message_service
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact
from brokerops_core.models.message import MessageStatus
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.message_send import MessageSendService, UnknownOutboundMessageError
from brokerops_langgraph.graphs.vapi_followup import build_vapi_followup

HOT_TRANSCRIPT = (
    "We loved the kitchen and the backyard was perfect. "
    "We are ready to write an offer. Our budget is between four fifty and five twenty five."
)
COOL_TRANSCRIPT = (
    "Nice house but it felt overpriced for the neighborhood. "
    "The bathroom needs updating. We will keep looking."
)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _input(transcript: str, call_id: str = "call-1") -> dict[str, Any]:
    return {
        "call_id": call_id,
        "listing_key": "RM1001",
        "contact_id": "101",
        "transcript": transcript,
        "call_outcome": "customer-ended-call",
    }


def _build(
    crm: GraphFakeCRM,
    store: FakeFeedbackStore,
    voice: FakeVoice | None = None,
    messages: MessageSendService | None = None,
) -> Any:
    return build_vapi_followup(
        voice or FakeVoice(),
        crm,
        store,
        DeterministicExtractor(),
        messages or make_message_service()[0],
        InMemorySaver(),
    )


def _crm_with_contact() -> GraphFakeCRM:
    crm = GraphFakeCRM()
    crm.contacts["101"] = Contact(crm_id="101", name="Jordan Pike", email="jordan@example.test")
    return crm


async def test_cool_call_syncs_feedback_and_crm_without_hitl() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    result = await _build(crm, store).ainvoke(_input(COOL_TRANSCRIPT), _config(uuid4().hex))
    assert result["outcome"] == "synced"
    assert "__interrupt__" not in result
    # feedback persisted, raw + extracted layers both stored
    feedback = store.feedback["FB-call-1"]
    assert feedback.sentiment.value == "negative"
    assert store.call_records["call-1"].extracted["price_opinion"] == "overpriced"
    # CRM got a note and a call log
    contact_id, subject, body = crm.notes[0]
    assert contact_id == "101" and "RM1001" in subject and "overpriced" in body
    assert crm.logged_calls[0][1] == "negative"
    assert crm.created_tasks == []


async def test_hot_call_pauses_then_creates_hot_task_on_approval() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    graph = _build(crm, store)
    config = _config(uuid4().hex)
    paused = await graph.ainvoke(_input(HOT_TRANSCRIPT, "call-2"), config)
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "notify_agent"
    assert "offer intent" in payload["reason"]
    # the CRM sync happened BEFORE the gate — note + call log already exist
    assert len(crm.notes) == 1

    result = await graph.ainvoke(
        Command(resume={"decision": "approved", "decided_by": "demo-operator"}), config
    )
    assert result["outcome"] == "agent_notified"
    hot_tasks = [t for t in crm.created_tasks if t.name.startswith("HOT LEAD")]
    assert len(hot_tasks) == 1
    assert result["hot_task_id"] == hot_tasks[0].id
    # resuming past the gate must NOT replay the pre-gate CRM sync — otherwise
    # every approved hot lead double-writes its note and call log to the CRM
    assert len(crm.notes) == 1
    assert len(crm.logged_calls) == 1


async def test_hot_signal_dismissed_creates_no_task() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    graph = _build(crm, store)
    config = _config(uuid4().hex)
    await graph.ainvoke(_input(HOT_TRANSCRIPT, "call-3"), config)
    result = await graph.ainvoke(
        Command(resume={"decision": "rejected", "decided_by": "demo-operator"}), config
    )
    assert result["outcome"] == "hot_signal_dismissed"
    assert crm.created_tasks == []


async def test_cool_call_with_reachable_contact_drafts_followup_gate() -> None:
    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    graph = _build(crm, store, messages=messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke(_input(COOL_TRANSCRIPT, "call-6"), config)
    # the CRM sync happened before the gate, unchanged
    assert len(crm.notes) == 1 and len(crm.logged_calls) == 1
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "approve_outbound_message"
    assert payload["channel"] == "email"
    assert payload["recipient"] == "jordan@example.test"
    assert "RM1001" in payload["subject"]
    assert message_store.rows[payload["message_id"]].status is MessageStatus.PENDING_APPROVAL
    assert email.sent == []

    result = await graph.ainvoke(
        Command(
            resume={
                "decision": "approved",
                "decided_by": "demo-operator",
                "edited_payload": {"body": "Edited follow-up body."},
            }
        ),
        config,
    )
    assert result["outcome"] == "followup_sent"
    # resuming past the gate must not replay the pre-gate CRM sync
    assert len(crm.notes) == 1 and len(crm.logged_calls) == 1
    assert [m.body for m in email.sent] == ["Edited follow-up body."]
    sent_row = message_store.rows[payload["message_id"]]
    assert sent_row.status is MessageStatus.SENT
    assert sent_row.body == "Edited follow-up body."


async def test_rejected_followup_email_sends_nothing() -> None:
    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    graph = _build(crm, store, messages=messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke(_input(COOL_TRANSCRIPT, "call-7"), config)
    payload = paused["__interrupt__"][0].value
    result = await graph.ainvoke(
        Command(resume={"decision": "rejected", "decided_by": "demo-operator"}), config
    )
    assert result["outcome"] == "followup_dismissed"
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.REJECTED
    # the feedback + CRM sync from before the gate stand
    assert store.feedback["FB-call-7"].sentiment.value == "negative"
    assert len(crm.notes) == 1


async def test_dangling_followup_message_id_raises_the_named_domain_error() -> None:
    # BOP-037: the gate node re-fetches the draft row on resume; a vanished row
    # must raise UnknownOutboundMessageError (mappable to a clean 409 upstream),
    # not an `assert` that AssertionErrors into a 500 — or vanishes under -O.
    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    graph = _build(crm, store, messages=messages)
    config = _config(uuid4().hex)

    paused = await graph.ainvoke(_input(COOL_TRANSCRIPT, "call-8"), config)
    payload = paused["__interrupt__"][0].value
    del message_store.rows[payload["message_id"]]
    with pytest.raises(UnknownOutboundMessageError):
        await graph.ainvoke(
            Command(resume={"decision": "approved", "decided_by": "demo-operator"}), config
        )
    assert email.sent == []


async def test_missing_transcript_falls_back_to_voice_port() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    voice = FakeVoice(
        calls={
            "call-4": CallRecord(
                vapi_call_id="call-4",
                contact_id="102",
                listing_key="RM1002",
                transcript=COOL_TRANSCRIPT,
                outcome="customer-ended-call",
            )
        }
    )
    result = await _build(crm, store, voice).ainvoke({"call_id": "call-4"}, _config(uuid4().hex))
    assert result["outcome"] == "synced"
    assert store.feedback["FB-call-4"].listing_key == "RM1002"


async def test_no_transcript_anywhere_ends_cleanly() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    result = await _build(crm, store).ainvoke({"call_id": "call-x"}, _config(uuid4().hex))
    assert result["outcome"] == "no_transcript"
    assert store.feedback == {}
    assert crm.notes == []
