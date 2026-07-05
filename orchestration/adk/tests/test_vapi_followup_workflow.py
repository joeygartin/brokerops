"""Scenario parity with the LangGraph vapi_followup suite, on ADK."""

from typing import Any

from workflow_fixtures import (
    FakeFeedbackStore,
    FakeVoice,
    GraphFakeCRM,
    final_state,
    make_engine,
    make_message_service,
)

from brokerops_adk.workflows.vapi_followup import build_vapi_followup
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact
from brokerops_core.models.message import MessageStatus
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.message_send import MessageSendService
from brokerops_core.services.workflow_runs import VAPI_FOLLOWUP

HOT_TRANSCRIPT = (
    "We loved the kitchen and the backyard was perfect. "
    "We are ready to write an offer. Our budget is between four fifty and five twenty five."
)
COOL_TRANSCRIPT = (
    "Nice house but it felt overpriced for the neighborhood. "
    "The bathroom needs updating. We will keep looking."
)


def _input(transcript: str, call_id: str = "call-1") -> dict[str, Any]:
    return {
        "call_id": call_id,
        "listing_key": "RM1001",
        "contact_id": "101",
        "transcript": transcript,
        "call_outcome": "customer-ended-call",
    }


def _decision(
    decision: ApprovalStatus, edited_payload: dict[str, Any] | None = None
) -> ApprovalDecision:
    return ApprovalDecision(
        decision=decision, decided_by="demo-operator", edited_payload=edited_payload
    )


def _workflow(
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
    )


def _crm_with_contact() -> GraphFakeCRM:
    crm = GraphFakeCRM()
    crm.contacts["101"] = Contact(crm_id="101", name="Jordan Pike", email="jordan@example.test")
    return crm


async def test_cool_call_syncs_feedback_and_crm_without_hitl() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, _ = make_engine(_workflow(crm, store))
    run = await engine.start(VAPI_FOLLOWUP, _input(COOL_TRANSCRIPT))
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "synced"
    assert run.approval is None
    # feedback persisted, raw + extracted layers both stored
    feedback = store.feedback["FB-call-1"]
    assert feedback.sentiment.value == "negative"
    record = store.call_records["call-1"]
    assert record.extracted is not None
    assert record.extracted["price_opinion"] == "overpriced"
    # CRM got a note and a call log
    contact_id, subject, body = crm.notes[0]
    assert contact_id == "101" and "RM1001" in subject and "overpriced" in body
    assert crm.logged_calls[0][1] == "negative"
    assert crm.created_tasks == []


async def test_hot_call_pauses_then_creates_hot_task_on_approval() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, sessions = make_engine(_workflow(crm, store))
    run = await engine.start(VAPI_FOLLOWUP, _input(HOT_TRANSCRIPT, "call-2"))
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    payload = run.approval.payload
    assert payload["kind"] == "notify_agent"
    assert "offer intent" in payload["reason"]
    # the CRM sync happened BEFORE the gate — note + call log already exist
    assert len(crm.notes) == 1

    result = await engine.decide(run.approval, _decision(ApprovalStatus.APPROVED))
    assert result.status == "completed"
    state = await final_state(sessions, VAPI_FOLLOWUP, run.thread_id)
    assert state["outcome"] == "agent_notified"
    hot_tasks = [t for t in crm.created_tasks if t.name.startswith("HOT LEAD")]
    assert len(hot_tasks) == 1
    assert state["hot_task_id"] == hot_tasks[0].id
    # only the gate node reruns on resume — the pre-gate CRM sync must not, or
    # every approved hot lead double-writes its note and call log to the CRM
    assert len(crm.notes) == 1
    assert len(crm.logged_calls) == 1


async def test_hot_signal_dismissed_creates_no_task() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, _ = make_engine(_workflow(crm, store))
    run = await engine.start(VAPI_FOLLOWUP, _input(HOT_TRANSCRIPT, "call-3"))
    assert run.approval is not None

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "hot_signal_dismissed"
    assert crm.created_tasks == []


async def test_cool_call_with_reachable_contact_drafts_followup_gate() -> None:
    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    engine, sessions = make_engine(_workflow(crm, store, messages=messages))

    run = await engine.start(VAPI_FOLLOWUP, _input(COOL_TRANSCRIPT, "call-6"))
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    payload = run.approval.payload
    assert payload["kind"] == "approve_outbound_message"
    assert payload["channel"] == "email"
    assert payload["recipient"] == "jordan@example.test"
    assert "RM1001" in payload["subject"]
    # the CRM sync happened before the gate; the draft is pending, unsent
    assert len(crm.notes) == 1 and len(crm.logged_calls) == 1
    assert message_store.rows[payload["message_id"]].status is MessageStatus.PENDING_APPROVAL
    assert email.sent == []

    result = await engine.decide(
        run.approval,
        _decision(ApprovalStatus.APPROVED, edited_payload={"body": "Edited follow-up body."}),
    )
    assert result.status == "completed"
    state = await final_state(sessions, VAPI_FOLLOWUP, run.thread_id)
    assert state["outcome"] == "followup_sent"
    # only the gate node reruns on resume — the pre-gate CRM sync must not
    assert len(crm.notes) == 1 and len(crm.logged_calls) == 1
    # the edited text is exactly what shipped, and the row records the send
    assert [m.body for m in email.sent] == ["Edited follow-up body."]
    sent_row = message_store.rows[payload["message_id"]]
    assert sent_row.status is MessageStatus.SENT
    assert sent_row.body == "Edited follow-up body."


async def test_rejected_followup_email_sends_nothing() -> None:
    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    engine, _ = make_engine(_workflow(crm, store, messages=messages))

    run = await engine.start(VAPI_FOLLOWUP, _input(COOL_TRANSCRIPT, "call-7"))
    assert run.approval is not None
    payload = run.approval.payload

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "followup_dismissed"
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.REJECTED
    # the feedback + CRM sync from before the gate stand
    assert store.feedback["FB-call-7"].sentiment.value == "negative"
    assert len(crm.notes) == 1


async def test_blank_edited_body_fails_loudly_instead_of_reverting() -> None:
    # F4 on ADK: a present-but-blank edited body fails the resume loudly and
    # nothing is sent — never a silent fallback to the original draft.
    import pytest

    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    engine, _ = make_engine(_workflow(crm, store, messages=messages))

    run = await engine.start(VAPI_FOLLOWUP, _input(COOL_TRANSCRIPT, "call-8"))
    assert run.approval is not None
    payload = run.approval.payload

    with pytest.raises(ValueError, match="blank"):
        await engine.decide(
            run.approval,
            _decision(ApprovalStatus.APPROVED, edited_payload={"body": "   "}),
        )
    assert email.sent == []
    assert message_store.rows[payload["message_id"]].status is MessageStatus.PENDING_APPROVAL


async def test_dangling_followup_message_id_raises_the_named_domain_error() -> None:
    # BOP-037: a decided gate whose draft row has vanished must surface the
    # named UnknownOutboundMessageError (mappable to a clean 409 upstream), not
    # an AssertionError/bare LookupError 500.
    import pytest

    from brokerops_core.services.message_send import UnknownOutboundMessageError

    crm, store = _crm_with_contact(), FakeFeedbackStore()
    messages, email, message_store = make_message_service()
    engine, _ = make_engine(_workflow(crm, store, messages=messages))

    run = await engine.start(VAPI_FOLLOWUP, _input(COOL_TRANSCRIPT, "call-9"))
    assert run.approval is not None
    del message_store.rows[run.approval.payload["message_id"]]

    with pytest.raises(UnknownOutboundMessageError):
        await engine.decide(run.approval, _decision(ApprovalStatus.APPROVED))
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
    engine, _ = make_engine(_workflow(crm, store, voice))
    run = await engine.start(VAPI_FOLLOWUP, {"call_id": "call-4"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "synced"
    assert store.feedback["FB-call-4"].listing_key == "RM1002"


async def test_no_transcript_anywhere_ends_cleanly() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, _ = make_engine(_workflow(crm, store))
    run = await engine.start(VAPI_FOLLOWUP, {"call_id": "call-x"})
    assert run.status == "no_transcript"
    assert store.feedback == {}
    assert crm.notes == []
