"""transaction_coordination — deadline-driven graph, one run per active transaction.

Triggered on a schedule (Cloud Scheduler → the cron endpoint), not by a user.
Routes on the worst milestone classification; all date math and rule logic
lives in core's milestone_engine. Overdue milestones pause at a HITL
escalation gate; approved escalations create URGENT CRM tasks and ratchet the
milestone's escalation level.
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
from brokerops_core.services.milestone_engine import (
    MilestoneClass,
    assess_milestones,
    draft_escalation_note,
    draft_milestone_reminder,
    worst_classification,
)
from brokerops_langgraph.state import ApprovalOutcome, TransactionCoordinationState

APPROVE_ESCALATION = "approve_escalation"


def build_transaction_coordination(
    store: TransactionStore, crm: CRMPort, checkpointer: BaseCheckpointSaver[Any]
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

    graph = StateGraph(TransactionCoordinationState)
    graph.add_node("load_txn", load_txn)
    graph.add_node("evaluate_milestones", evaluate_milestones)
    graph.add_node("log_on_track", log_on_track)
    graph.add_node("draft_reminders", draft_reminders)
    graph.add_node("send_reminders", send_reminders)
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
    graph.add_conditional_edges("escalate", route_after_escalation, ["notify", "dismiss"])
    for terminal in ("log_on_track", "send_reminders", "notify", "dismiss", "queue_vapi_call"):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=checkpointer)
