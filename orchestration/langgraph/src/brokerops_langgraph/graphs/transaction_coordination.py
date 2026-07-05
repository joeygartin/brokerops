"""transaction_coordination — deadline-driven graph, one run per active transaction.

Triggered on a schedule (Cloud Scheduler → the cron endpoint), not by a user.
Routes on the worst milestone classification; all date math and rule logic
lives in core's milestone_engine. Overdue milestones pause at a HITL
escalation gate; approved escalations create URGENT CRM tasks and ratchet the
milestone's escalation level. The due-soon path additionally drafts a
milestone-reminder email to the reachable external party (BOP-019): the draft
pauses at an approve-outbound-message gate, and only the human decision — with
any edited text — sends it through the seam-wrapped EmailPort.
"""

from datetime import date
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from brokerops_core.models.milestone import Milestone
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.drafting import edited_draft_fields
from brokerops_core.services.message_send import MessageSendService, UnknownOutboundMessageError
from brokerops_core.services.milestone_engine import (
    MilestoneClass,
    assess_milestones,
    draft_escalation_note,
    draft_milestone_reminder,
    plan_reminder_email,
    worst_classification,
)
from brokerops_langgraph.state import ApprovalOutcome, TransactionCoordinationState

APPROVE_ESCALATION = "approve_escalation"
APPROVE_OUTBOUND_MESSAGE = "approve_outbound_message"


def build_transaction_coordination(
    store: TransactionStore,
    crm: CRMPort,
    messages: MessageSendService,
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledStateGraph[Any, Any, Any, Any]:
    async def _milestones_by_class(
        state: TransactionCoordinationState, classification: MilestoneClass
    ) -> list[tuple[Milestone, int]]:
        """Re-fetch milestones and pair the ones in `classification` with days-until-due."""
        milestones = {m.id: m for m in await store.list_milestones(state.transaction_id)}
        return [
            (milestones[a["milestone_id"]], int(a["days_until_due"]))
            for a in state.assessments
            if a["classification"] == classification.value and a["milestone_id"] in milestones
        ]

    async def load_txn(state: TransactionCoordinationState) -> dict[str, Any]:
        txn = await store.get_transaction(state.transaction_id)
        return {"outcome": "not_found"} if txn is None else {}

    async def evaluate_milestones(state: TransactionCoordinationState) -> dict[str, Any]:
        milestones = await store.list_milestones(state.transaction_id)
        assessments = assess_milestones(milestones, date.today())
        worst = worst_classification(assessments)
        return {
            "assessments": [a.model_dump(mode="json") for a in assessments],
            "worst": worst.value,
        }

    async def log_on_track(state: TransactionCoordinationState) -> dict[str, Any]:
        return {"outcome": "on_track"}

    async def draft_reminders(state: TransactionCoordinationState) -> dict[str, Any]:
        txn = await store.get_transaction(state.transaction_id)
        assert txn is not None
        due_soon = await _milestones_by_class(state, MilestoneClass.DUE_SOON)
        return {"reminders": [draft_milestone_reminder(txn, m, days) for m, days in due_soon]}

    async def send_reminders(state: TransactionCoordinationState) -> dict[str, Any]:
        due_soon = await _milestones_by_class(state, MilestoneClass.DUE_SOON)
        task_ids = [
            (await crm.create_task(text, due_date=milestone.due_date)).id
            for text, (milestone, _) in zip(state.reminders, due_soon, strict=True)
        ]
        return {"reminder_task_ids": task_ids, "outcome": "reminders_sent"}

    async def draft_reminder_email(state: TransactionCoordinationState) -> dict[str, Any]:
        # Additive tail on the due-soon path (BOP-019): the CRM tasks above are
        # unchanged; this drafts the reminder email when a reachable external
        # party exists (plan_reminder_email owns that rule), else skips.
        if state.suppress_reminder_email:
            # A pending outbound-message gate already exists for this
            # transaction (cron sets the flag): skip only this tail so gates
            # don't stack — everything before this node already ran.
            return {}
        txn = await store.get_transaction(state.transaction_id)
        assert txn is not None
        due_soon = await _milestones_by_class(state, MilestoneClass.DUE_SOON)
        context = plan_reminder_email(txn, due_soon)
        if context is None:
            return {}
        message = await messages.draft_for_approval(context)
        return {"reminder_message_id": message.id}

    async def approve_reminder_email(state: TransactionCoordinationState) -> dict[str, Any]:
        message = await messages.get_message(state.reminder_message_id)
        if message is None:
            # A named domain error (BOP-037), not an assert: the row vanishing
            # under the gate must surface as a clean state conflict — and an
            # assert disappears entirely under `python -O`.
            raise UnknownOutboundMessageError(state.reminder_message_id)
        decision: dict[str, Any] = interrupt(
            {
                "kind": APPROVE_OUTBOUND_MESSAGE,
                "message_id": message.id,
                "channel": message.channel.value,
                "recipient": message.recipient,
                "subject": message.subject,
                "body": message.body,
                "template_ref": message.template_ref,
                "transaction_id": state.transaction_id,
                "listing_key": message.listing_key,
            }
        )
        updates: dict[str, Any] = {"reminder_approval": ApprovalOutcome.model_validate(decision)}
        # Raises on a present-but-blank body (never silently fall back to the
        # original draft); the frontend card blocks this before it gets here.
        subject_edit, body_edit = edited_draft_fields(decision.get("edited_payload"))
        if subject_edit:
            updates["reminder_edited_subject"] = subject_edit
        if body_edit:
            updates["reminder_edited_body"] = body_edit
        return updates

    async def send_reminder_email(state: TransactionCoordinationState) -> dict[str, Any]:
        await messages.send_approved(
            state.reminder_message_id,
            subject=state.reminder_edited_subject or None,
            body=state.reminder_edited_body or None,
        )
        return {"outcome": "reminder_email_sent"}

    async def dismiss_reminder_email(state: TransactionCoordinationState) -> dict[str, Any]:
        await messages.mark_rejected(state.reminder_message_id)
        return {"outcome": "reminder_email_dismissed"}

    async def escalate(state: TransactionCoordinationState) -> dict[str, Any]:
        txn = await store.get_transaction(state.transaction_id)
        assert txn is not None
        overdue = await _milestones_by_class(state, MilestoneClass.OVERDUE)
        decision: dict[str, Any] = interrupt(
            {
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
            }
        )
        return {"escalation_approval": ApprovalOutcome.model_validate(decision)}

    async def notify(state: TransactionCoordinationState) -> dict[str, Any]:
        txn = await store.get_transaction(state.transaction_id)
        assert txn is not None
        task_ids: list[str] = []
        for milestone, days in await _milestones_by_class(state, MilestoneClass.OVERDUE):
            note = draft_escalation_note(txn, milestone, -days)
            task = await crm.create_task(f"URGENT: {note}", due_date=date.today())
            task_ids.append(task.id)
            await store.set_escalation_level(milestone.id, milestone.escalation_level + 1)
        return {"escalated_task_ids": task_ids, "outcome": "escalated"}

    async def dismiss(state: TransactionCoordinationState) -> dict[str, Any]:
        return {"outcome": "escalation_dismissed"}

    async def queue_vapi_call(state: TransactionCoordinationState) -> dict[str, Any]:
        # Records call intent only — the voice integration (VoicePort) places
        # these calls once the Vapi integration lands.
        blocked = await _milestones_by_class(state, MilestoneClass.BLOCKED_EXTERNAL)
        calls = [
            f"Status-check call for {m.title} ({m.blocked_reason}) — owner {m.owner or 'unknown'}"
            for m, _ in blocked
        ]
        return {"planned_calls": calls, "outcome": "call_queued"}

    def route_after_load(state: TransactionCoordinationState) -> str:
        return END if state.outcome == "not_found" else "evaluate_milestones"

    def route_by_severity(state: TransactionCoordinationState) -> str:
        return {
            MilestoneClass.OVERDUE.value: "escalate",
            MilestoneClass.DUE_SOON.value: "draft_reminders",
            MilestoneClass.BLOCKED_EXTERNAL.value: "queue_vapi_call",
            MilestoneClass.ON_TRACK.value: "log_on_track",
        }[state.worst]

    def route_after_escalation(state: TransactionCoordinationState) -> str:
        assert state.escalation_approval is not None
        return "notify" if state.escalation_approval.decision.value == "approved" else "dismiss"

    def route_after_draft_email(state: TransactionCoordinationState) -> str:
        return APPROVE_OUTBOUND_MESSAGE if state.reminder_message_id else END

    def route_after_message_gate(state: TransactionCoordinationState) -> str:
        assert state.reminder_approval is not None
        approved = state.reminder_approval.decision.value == "approved"
        return "send_reminder_email" if approved else "dismiss_reminder_email"

    graph = StateGraph(TransactionCoordinationState)
    graph.add_node("load_txn", load_txn)
    graph.add_node("evaluate_milestones", evaluate_milestones)
    graph.add_node("log_on_track", log_on_track)
    graph.add_node("draft_reminders", draft_reminders)
    graph.add_node("send_reminders", send_reminders)
    graph.add_node("draft_reminder_email", draft_reminder_email)
    graph.add_node(APPROVE_OUTBOUND_MESSAGE, approve_reminder_email)
    graph.add_node("send_reminder_email", send_reminder_email)
    graph.add_node("dismiss_reminder_email", dismiss_reminder_email)
    graph.add_node("escalate", escalate)
    graph.add_node("notify", notify)
    graph.add_node("dismiss", dismiss)
    graph.add_node("queue_vapi_call", queue_vapi_call)

    graph.add_edge(START, "load_txn")
    graph.add_conditional_edges("load_txn", route_after_load, ["evaluate_milestones", END])
    graph.add_conditional_edges(
        "evaluate_milestones",
        route_by_severity,
        ["escalate", "draft_reminders", "queue_vapi_call", "log_on_track"],
    )
    graph.add_edge("draft_reminders", "send_reminders")
    graph.add_edge("send_reminders", "draft_reminder_email")
    graph.add_conditional_edges(
        "draft_reminder_email", route_after_draft_email, [APPROVE_OUTBOUND_MESSAGE, END]
    )
    graph.add_conditional_edges(
        APPROVE_OUTBOUND_MESSAGE,
        route_after_message_gate,
        ["send_reminder_email", "dismiss_reminder_email"],
    )
    graph.add_conditional_edges("escalate", route_after_escalation, ["notify", "dismiss"])
    for terminal in (
        "log_on_track",
        "send_reminder_email",
        "dismiss_reminder_email",
        "notify",
        "dismiss",
        "queue_vapi_call",
    ):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=checkpointer)
