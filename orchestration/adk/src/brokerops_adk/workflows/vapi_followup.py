"""vapi_followup — ADK port of the webhook-driven feedback workflow.

ingest → structured extraction (Pydantic-validated, core service) → persist
feedback → sync the CRM (note + call log) → if the buyer signaled offer
intent, pause at a HITL notify-agent gate; approval creates a hot-lead task.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, FunctionNode, Workflow

from brokerops_adk.interrupts import request_input
from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import FeedbackSource, ShowingFeedback
from brokerops_core.models.workflow_state import ApprovalOutcome, VapiFollowupState
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.extraction import ExtractionPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.feedback_extraction import ExtractedFeedback

NOTIFY_AGENT = "notify_agent"

# A route value with no matching edge ends the run — the ADK spelling of END.
STOP = "stop"


def build_vapi_followup(
    voice: VoicePort,
    crm: CRMPort,
    feedback_store: FeedbackStore,
    extraction: ExtractionPort,
) -> Workflow:
    async def ingest_call(
        ctx: Context,
        call_id: str,
        transcript: str = "",
        listing_key: str = "",
        contact_id: str = "",
        call_outcome: str = "",
    ) -> None:
        if not transcript:
            # Webhook payloads carry the transcript; fall back to the API for
            # manually replayed/polled calls.
            record = await voice.get_call(call_id)
            if record is not None:
                transcript = record.transcript
                listing_key = listing_key or record.listing_key
                contact_id = contact_id or record.contact_id
                call_outcome = call_outcome or record.outcome
        if not transcript:
            ctx.state["outcome"] = "no_transcript"
            ctx.route = STOP
            return
        await feedback_store.save_call_record(
            CallRecord(
                vapi_call_id=call_id,
                contact_id=contact_id,
                listing_key=listing_key,
                transcript=transcript,
                outcome=call_outcome,
                created_at=datetime.now(UTC),
            )
        )
        ctx.state["transcript"] = transcript
        ctx.state["listing_key"] = listing_key
        ctx.state["contact_id"] = contact_id
        ctx.state["call_outcome"] = call_outcome
        ctx.route = "ingested"

    async def extract_structured(ctx: Context, transcript: str) -> None:
        extracted = await extraction.extract(transcript)
        ctx.state["extracted"] = extracted.model_dump(mode="json")

    async def upsert_feedback(
        ctx: Context,
        call_id: str,
        listing_key: str,
        contact_id: str,
        extracted: dict[str, Any],
    ) -> None:
        parsed = ExtractedFeedback.model_validate(extracted)
        feedback = ShowingFeedback(
            id=f"FB-{call_id}",
            listing_key=listing_key,
            contact_id=contact_id,
            call_id=call_id,
            source=FeedbackSource.CALL,
            sentiment=parsed.sentiment,
            structured_answers=extracted,
            created_at=datetime.now(UTC),
        )
        feedback_id = await feedback_store.upsert_feedback(feedback)
        record = await feedback_store.get_call_record(call_id)
        if record is not None:
            await feedback_store.save_call_record(
                record.model_copy(update={"extracted": extracted})
            )
        ctx.state["feedback_id"] = feedback_id

    async def sync_crm(
        ctx: Context,
        call_id: str,
        listing_key: str,
        contact_id: str,
        extracted: dict[str, Any],
    ) -> None:
        parsed = ExtractedFeedback.model_validate(extracted)
        note_id = await crm.add_note(
            contact_id,
            subject=f"Showing feedback — {listing_key}",
            body=parsed.summary,
        )
        call_log_id = await crm.log_call(
            contact_id,
            outcome=parsed.sentiment.value,
            note=f"Feedback call for {listing_key} ({call_id})",
        )
        ctx.state["note_id"] = note_id
        ctx.state["call_log_id"] = call_log_id
        ctx.route = "hot" if parsed.hot_signal else "synced"

    async def notify_agent(
        ctx: Context,
        call_id: str,
        listing_key: str,
        contact_id: str,
        extracted: dict[str, Any],
    ) -> AsyncGenerator[RequestInput, None]:
        decision: dict[str, Any] | None = ctx.resume_inputs.get(NOTIFY_AGENT)
        if decision is None:
            parsed = ExtractedFeedback.model_validate(extracted)
            yield request_input(
                NOTIFY_AGENT,
                payload={
                    "kind": NOTIFY_AGENT,
                    "listing_key": listing_key,
                    "contact_id": contact_id,
                    "call_id": call_id,
                    "reason": "Buyer signaled offer intent on the feedback call",
                    "summary": parsed.summary,
                },
            )
            return
        outcome = ApprovalOutcome.model_validate(decision)
        ctx.state["hot_approval"] = outcome.model_dump(mode="json")
        ctx.route = "approved" if outcome.decision.value == "approved" else "dismissed"

    async def create_hot_task(ctx: Context, listing_key: str, contact_id: str) -> None:
        task = await crm.create_task(
            f"HOT LEAD: contact {contact_id} signaled offer intent on "
            f"{listing_key} — call back today",
            due_date=date.today(),
            contact_id=contact_id or None,
        )
        ctx.state["hot_task_id"] = task.id
        ctx.state["outcome"] = "agent_notified"

    async def finish_synced(ctx: Context) -> None:
        ctx.state["outcome"] = "synced"

    async def dismiss_hot(ctx: Context) -> None:
        ctx.state["outcome"] = "hot_signal_dismissed"

    n_ingest = FunctionNode(func=ingest_call)
    n_extract = FunctionNode(func=extract_structured)
    n_upsert = FunctionNode(func=upsert_feedback)
    n_sync = FunctionNode(func=sync_crm)
    n_notify = FunctionNode(func=notify_agent, rerun_on_resume=True)
    n_hot_task = FunctionNode(func=create_hot_task)
    n_synced = FunctionNode(func=finish_synced)
    n_dismiss = FunctionNode(func=dismiss_hot)

    return Workflow(
        name="vapi_followup",
        state_schema=VapiFollowupState,
        edges=[
            (START, n_ingest, {"ingested": n_extract}),
            (n_extract, n_upsert, n_sync, {"hot": n_notify, "synced": n_synced}),
            (n_notify, {"approved": n_hot_task, "dismissed": n_dismiss}),
        ],
    )
