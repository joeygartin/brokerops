"""transaction_coordination — ADK port of the deadline-driven workflow.

Triggered on a schedule (Cloud Scheduler → the cron endpoint), not by a user.
Routes on the worst milestone classification; all date math and rule logic
lives in core's milestone_engine. Overdue milestones pause at a HITL
escalation gate; approved escalations create URGENT CRM tasks and ratchet the
milestone's escalation level. The due-soon path additionally drafts a
milestone-reminder email to the reachable external party (BOP-019): the draft
pauses at an approve-outbound-message gate — the gate node reruns on resume
and stays read-only; the send/reject side effects live in the post-decision
nodes.
"""

from collections.abc import AsyncGenerator
from datetime import date
from typing import Any

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, FunctionNode, Workflow

from brokerops_adk.interrupts import request_input
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.workflow_state import ApprovalOutcome, TransactionCoordinationState
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.drafting import edited_draft_fields
from brokerops_core.services.message_send import MessageSendService
from brokerops_core.services.milestone_engine import (
    MilestoneClass,
    assess_milestones,
    draft_escalation_note,
    draft_milestone_reminder,
    plan_reminder_email,
    worst_classification,
)

APPROVE_ESCALATION = "approve_escalation"
APPROVE_OUTBOUND_MESSAGE = "approve_outbound_message"

# A route value with no matching edge ends the run — the ADK spelling of END.
STOP = "stop"


def build_transaction_coordination(
    store: TransactionStore, crm: CRMPort, messages: MessageSendService
) -> Workflow:
    async def _milestones_by_class(
        transaction_id: str, assessments: list[dict[str, Any]], classification: MilestoneClass
    ) -> list[tuple[Milestone, int]]:
        """Re-fetch milestones and pair the ones in `classification` with days-until-due."""
        milestones = {m.id: m for m in await store.list_milestones(transaction_id)}
        return [
            (milestones[a["milestone_id"]], int(a["days_until_due"]))
            for a in assessments
            if a["classification"] == classification.value and a["milestone_id"] in milestones
        ]

    async def load_txn(ctx: Context, transaction_id: str) -> None:
        txn = await store.get_transaction(transaction_id)
        if txn is None:
            ctx.state["outcome"] = "not_found"
            ctx.route = STOP
            return
        ctx.route = "found"

    async def evaluate_milestones(ctx: Context, transaction_id: str) -> None:
        milestones = await store.list_milestones(transaction_id)
        assessments = assess_milestones(milestones, date.today())
        worst = worst_classification(assessments)
        ctx.state["assessments"] = [a.model_dump(mode="json") for a in assessments]
        ctx.state["worst"] = worst.value
        ctx.route = worst.value

    async def log_on_track(ctx: Context) -> None:
        ctx.state["outcome"] = "on_track"

    async def draft_reminders(
        ctx: Context, transaction_id: str, assessments: list[dict[str, Any]]
    ) -> None:
        txn = await store.get_transaction(transaction_id)
        assert txn is not None
        due_soon = await _milestones_by_class(transaction_id, assessments, MilestoneClass.DUE_SOON)
        ctx.state["reminders"] = [draft_milestone_reminder(txn, m, days) for m, days in due_soon]

    async def send_reminders(
        ctx: Context,
        transaction_id: str,
        assessments: list[dict[str, Any]],
        reminders: list[str],
    ) -> None:
        due_soon = await _milestones_by_class(transaction_id, assessments, MilestoneClass.DUE_SOON)
        task_ids = [
            (await crm.create_task(text, due_date=milestone.due_date)).id
            for text, (milestone, _) in zip(reminders, due_soon, strict=True)
        ]
        ctx.state["reminder_task_ids"] = task_ids
        ctx.state["outcome"] = "reminders_sent"

    async def draft_reminder_email(
        ctx: Context,
        transaction_id: str,
        assessments: list[dict[str, Any]],
        suppress_reminder_email: bool = False,
    ) -> None:
        # Additive tail on the due-soon path (BOP-019): the CRM tasks above are
        # unchanged; this drafts the reminder email when a reachable external
        # party exists (plan_reminder_email owns that rule), else skips.
        if suppress_reminder_email:
            # A pending outbound-message gate already exists for this
            # transaction (cron sets the flag): skip only this tail so gates
            # don't stack — everything before this node already ran.
            ctx.route = STOP
            return
        txn = await store.get_transaction(transaction_id)
        assert txn is not None
        due_soon = await _milestones_by_class(transaction_id, assessments, MilestoneClass.DUE_SOON)
        context = plan_reminder_email(txn, due_soon)
        if context is None:
            ctx.route = STOP
            return
        message = await messages.draft_for_approval(context)
        ctx.state["reminder_message_id"] = message.id
        ctx.route = "drafted"

    async def approve_reminder_email(
        ctx: Context, transaction_id: str, reminder_message_id: str
    ) -> AsyncGenerator[RequestInput, None]:
        # Reruns on resume — reads only; the send/reject side effects live in
        # the post-decision nodes below.
        decision: dict[str, Any] | None = ctx.resume_inputs.get(APPROVE_OUTBOUND_MESSAGE)
        if decision is None:
            message = await messages.get_message(reminder_message_id)
            assert message is not None
            yield request_input(
                APPROVE_OUTBOUND_MESSAGE,
                payload={
                    "kind": APPROVE_OUTBOUND_MESSAGE,
                    "message_id": message.id,
                    "channel": message.channel.value,
                    "recipient": message.recipient,
                    "subject": message.subject,
                    "body": message.body,
                    "template_ref": message.template_ref,
                    "transaction_id": transaction_id,
                    "listing_key": message.listing_key,
                },
            )
            return
        outcome = ApprovalOutcome.model_validate(decision)
        ctx.state["reminder_approval"] = outcome.model_dump(mode="json")
        # Raises on a present-but-blank body (never silently fall back to the
        # original draft); the frontend card blocks this before it gets here.
        subject_edit, body_edit = edited_draft_fields(decision.get("edited_payload"))
        if subject_edit:
            ctx.state["reminder_edited_subject"] = subject_edit
        if body_edit:
            ctx.state["reminder_edited_body"] = body_edit
        ctx.route = "approved" if outcome.decision.value == "approved" else "dismissed"

    async def send_reminder_email(ctx: Context, reminder_message_id: str) -> None:
        await messages.send_approved(
            reminder_message_id,
            subject=str(ctx.state.get("reminder_edited_subject") or "") or None,
            body=str(ctx.state.get("reminder_edited_body") or "") or None,
        )
        ctx.state["outcome"] = "reminder_email_sent"

    async def dismiss_reminder_email(ctx: Context, reminder_message_id: str) -> None:
        await messages.mark_rejected(reminder_message_id)
        ctx.state["outcome"] = "reminder_email_dismissed"

    async def escalate(
        ctx: Context, transaction_id: str, assessments: list[dict[str, Any]]
    ) -> AsyncGenerator[RequestInput, None]:
        txn = await store.get_transaction(transaction_id)
        assert txn is not None
        decision: dict[str, Any] | None = ctx.resume_inputs.get(APPROVE_ESCALATION)
        if decision is None:
            overdue = await _milestones_by_class(
                transaction_id, assessments, MilestoneClass.OVERDUE
            )
            yield request_input(
                APPROVE_ESCALATION,
                payload={
                    "kind": APPROVE_ESCALATION,
                    "transaction_id": txn.id,
                    "listing_key": txn.listing_key,
                    "milestones": [
                        {
                            "id": milestone.id,
                            "title": milestone.title,
                            "due_date": milestone.due_date.isoformat(),
                            "days_overdue": -days,
                            "escalation_level": milestone.escalation_level,
                            "note": draft_escalation_note(txn, milestone, -days),
                        }
                        for milestone, days in overdue
                    ],
                },
            )
            return
        outcome = ApprovalOutcome.model_validate(decision)
        ctx.state["escalation_approval"] = outcome.model_dump(mode="json")
        ctx.route = "approved" if outcome.decision.value == "approved" else "dismissed"

    async def notify(ctx: Context, transaction_id: str, assessments: list[dict[str, Any]]) -> None:
        txn = await store.get_transaction(transaction_id)
        assert txn is not None
        task_ids: list[str] = []
        overdue = await _milestones_by_class(transaction_id, assessments, MilestoneClass.OVERDUE)
        for milestone, days in overdue:
            note = draft_escalation_note(txn, milestone, -days)
            task = await crm.create_task(f"URGENT: {note}", due_date=date.today())
            task_ids.append(task.id)
            await store.set_escalation_level(milestone.id, milestone.escalation_level + 1)
        ctx.state["escalated_task_ids"] = task_ids
        ctx.state["outcome"] = "escalated"

    async def dismiss(ctx: Context) -> None:
        ctx.state["outcome"] = "escalation_dismissed"

    async def queue_vapi_call(
        ctx: Context, transaction_id: str, assessments: list[dict[str, Any]]
    ) -> None:
        # Records call intent only — the voice integration (VoicePort) places
        # these calls once the Vapi integration lands.
        blocked = await _milestones_by_class(
            transaction_id, assessments, MilestoneClass.BLOCKED_EXTERNAL
        )
        calls = [
            f"Status-check call for {m.title} ({m.blocked_reason}) — owner {m.owner or 'unknown'}"
            for m, _ in blocked
        ]
        ctx.state["planned_calls"] = calls
        ctx.state["outcome"] = "call_queued"

    n_load = FunctionNode(func=load_txn)
    n_evaluate = FunctionNode(func=evaluate_milestones)
    n_on_track = FunctionNode(func=log_on_track)
    n_draft_reminders = FunctionNode(func=draft_reminders)
    n_send_reminders = FunctionNode(func=send_reminders)
    n_draft_email = FunctionNode(func=draft_reminder_email)
    n_approve_email = FunctionNode(func=approve_reminder_email, rerun_on_resume=True)
    n_send_email = FunctionNode(func=send_reminder_email)
    n_dismiss_email = FunctionNode(func=dismiss_reminder_email)
    n_escalate = FunctionNode(func=escalate, rerun_on_resume=True)
    n_notify = FunctionNode(func=notify)
    n_dismiss = FunctionNode(func=dismiss)
    n_queue_call = FunctionNode(func=queue_vapi_call)

    return Workflow(
        name="transaction_coordination",
        state_schema=TransactionCoordinationState,
        edges=[
            (START, n_load, {"found": n_evaluate}),
            (
                n_evaluate,
                {
                    MilestoneClass.OVERDUE.value: n_escalate,
                    MilestoneClass.DUE_SOON.value: n_draft_reminders,
                    MilestoneClass.BLOCKED_EXTERNAL.value: n_queue_call,
                    MilestoneClass.ON_TRACK.value: n_on_track,
                },
            ),
            (n_draft_reminders, n_send_reminders, n_draft_email, {"drafted": n_approve_email}),
            (n_approve_email, {"approved": n_send_email, "dismissed": n_dismiss_email}),
            (n_escalate, {"approved": n_notify, "dismissed": n_dismiss}),
        ],
    )
