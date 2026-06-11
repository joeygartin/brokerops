"""vapi_followup — webhook-driven graph: one run per completed feedback call.

ingest → structured extraction (Pydantic-validated, core service) → persist
feedback → sync the CRM (note + call log) → if the buyer signaled offer
intent, pause at a HITL notify-agent gate; approval creates a hot-lead task.
"""

from datetime import UTC, date, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import FeedbackSource, ShowingFeedback
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.feedback_extraction import ExtractedFeedback, extract_feedback
from brokerops_langgraph.state import ApprovalOutcome, VapiFollowupState

NOTIFY_AGENT = "notify_agent"


def build_vapi_followup(
    voice: VoicePort,
    crm: CRMPort,
    feedback_store: FeedbackStore,
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledStateGraph[Any, Any, Any, Any]:
    async def ingest_call(state: VapiFollowupState) -> dict[str, Any]:
        transcript = state.transcript
        listing_key = state.listing_key
        contact_id = state.contact_id
        outcome = state.call_outcome
        if not transcript:
            # Webhook payloads carry the transcript; fall back to the API for
            # manually replayed/polled calls.
            record = await voice.get_call(state.call_id)
            if record is not None:
                transcript = record.transcript
                listing_key = listing_key or record.listing_key
                contact_id = contact_id or record.contact_id
                outcome = outcome or record.outcome
        if not transcript:
            return {"outcome": "no_transcript"}
        await feedback_store.save_call_record(
            CallRecord(
                vapi_call_id=state.call_id,
                contact_id=contact_id,
                listing_key=listing_key,
                transcript=transcript,
                outcome=outcome,
                created_at=datetime.now(UTC),
            )
        )
        return {
            "transcript": transcript,
            "listing_key": listing_key,
            "contact_id": contact_id,
            "call_outcome": outcome,
        }

    async def extract_structured(state: VapiFollowupState) -> dict[str, Any]:
        extracted = extract_feedback(state.transcript)
        return {"extracted": extracted.model_dump(mode="json")}

    async def upsert_feedback(state: VapiFollowupState) -> dict[str, Any]:
        extracted = ExtractedFeedback.model_validate(state.extracted)
        feedback = ShowingFeedback(
            id=f"FB-{state.call_id}",
            listing_key=state.listing_key,
            contact_id=state.contact_id,
            call_id=state.call_id,
            source=FeedbackSource.CALL,
            sentiment=extracted.sentiment,
            structured_answers=state.extracted,
            created_at=datetime.now(UTC),
        )
        feedback_id = await feedback_store.upsert_feedback(feedback)
        record = await feedback_store.get_call_record(state.call_id)
        if record is not None:
            await feedback_store.save_call_record(
                record.model_copy(update={"extracted": state.extracted})
            )
        return {"feedback_id": feedback_id}

    async def sync_crm(state: VapiFollowupState) -> dict[str, Any]:
        extracted = ExtractedFeedback.model_validate(state.extracted)
        note_id = await crm.add_note(
            state.contact_id,
            subject=f"Showing feedback — {state.listing_key}",
            body=extracted.summary,
        )
        call_log_id = await crm.log_call(
            state.contact_id,
            outcome=extracted.sentiment.value,
            note=f"Feedback call for {state.listing_key} ({state.call_id})",
        )
        return {"note_id": note_id, "call_log_id": call_log_id}

    async def notify_agent(state: VapiFollowupState) -> dict[str, Any]:
        extracted = ExtractedFeedback.model_validate(state.extracted)
        decision: dict[str, Any] = interrupt(
            {
                "kind": NOTIFY_AGENT,
                "listing_key": state.listing_key,
                "contact_id": state.contact_id,
                "call_id": state.call_id,
                "reason": "Buyer signaled offer intent on the feedback call",
                "summary": extracted.summary,
            }
        )
        return {"hot_approval": ApprovalOutcome.model_validate(decision)}

    async def create_hot_task(state: VapiFollowupState) -> dict[str, Any]:
        task = await crm.create_task(
            f"HOT LEAD: contact {state.contact_id} signaled offer intent on "
            f"{state.listing_key} — call back today",
            due_date=date.today(),
            contact_id=state.contact_id or None,
        )
        return {"hot_task_id": task.id, "outcome": "agent_notified"}

    async def finish_synced(state: VapiFollowupState) -> dict[str, Any]:
        return {"outcome": "synced"}

    async def dismiss_hot(state: VapiFollowupState) -> dict[str, Any]:
        return {"outcome": "hot_signal_dismissed"}

    def route_after_ingest(state: VapiFollowupState) -> str:
        return END if state.outcome == "no_transcript" else "extract_structured"

    def route_after_sync(state: VapiFollowupState) -> str:
        extracted = ExtractedFeedback.model_validate(state.extracted)
        return "notify_agent" if extracted.hot_signal else "finish_synced"

    def route_after_notify(state: VapiFollowupState) -> str:
        assert state.hot_approval is not None
        approved = state.hot_approval.decision.value == "approved"
        return "create_hot_task" if approved else "dismiss_hot"

    graph = StateGraph(VapiFollowupState)
    graph.add_node("ingest_call", ingest_call)
    graph.add_node("extract_structured", extract_structured)
    graph.add_node("upsert_feedback", upsert_feedback)
    graph.add_node("sync_crm", sync_crm)
    graph.add_node(NOTIFY_AGENT, notify_agent)
    graph.add_node("create_hot_task", create_hot_task)
    graph.add_node("finish_synced", finish_synced)
    graph.add_node("dismiss_hot", dismiss_hot)

    graph.add_edge(START, "ingest_call")
    graph.add_conditional_edges("ingest_call", route_after_ingest, ["extract_structured", END])
    graph.add_edge("extract_structured", "upsert_feedback")
    graph.add_edge("upsert_feedback", "sync_crm")
    graph.add_conditional_edges("sync_crm", route_after_sync, [NOTIFY_AGENT, "finish_synced"])
    graph.add_conditional_edges(
        NOTIFY_AGENT, route_after_notify, ["create_hot_task", "dismiss_hot"]
    )
    for terminal in ("create_hot_task", "finish_synced", "dismiss_hot"):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=checkpointer)
