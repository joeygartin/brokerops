"""Scenario parity with the LangGraph vapi_followup suite, on ADK."""

from typing import Any

from workflow_fixtures import FakeFeedbackStore, FakeVoice, GraphFakeCRM, final_state, make_engine

from brokerops_adk.workflows.vapi_followup import build_vapi_followup
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.services.feedback_extraction import DeterministicExtractor
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


def _decision(decision: ApprovalStatus) -> ApprovalDecision:
    return ApprovalDecision(decision=decision, decided_by="demo-operator")


async def test_cool_call_syncs_feedback_and_crm_without_hitl() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, _ = make_engine(build_vapi_followup(FakeVoice(), crm, store, DeterministicExtractor()))
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
    engine, sessions = make_engine(
        build_vapi_followup(FakeVoice(), crm, store, DeterministicExtractor())
    )
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
    engine, _ = make_engine(build_vapi_followup(FakeVoice(), crm, store, DeterministicExtractor()))
    run = await engine.start(VAPI_FOLLOWUP, _input(HOT_TRANSCRIPT, "call-3"))
    assert run.approval is not None

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "hot_signal_dismissed"
    assert crm.created_tasks == []


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
    engine, _ = make_engine(build_vapi_followup(voice, crm, store, DeterministicExtractor()))
    run = await engine.start(VAPI_FOLLOWUP, {"call_id": "call-4"})
    assert run.status == "completed"
    assert run.output is not None and run.output["outcome"] == "synced"
    assert store.feedback["FB-call-4"].listing_key == "RM1002"


async def test_no_transcript_anywhere_ends_cleanly() -> None:
    crm, store = GraphFakeCRM(), FakeFeedbackStore()
    engine, _ = make_engine(build_vapi_followup(FakeVoice(), crm, store, DeterministicExtractor()))
    run = await engine.start(VAPI_FOLLOWUP, {"call_id": "call-x"})
    assert run.status == "no_transcript"
    assert store.feedback == {}
    assert crm.notes == []
