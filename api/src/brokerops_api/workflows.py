"""Workflow engine — runs graphs and translates interrupts into ApprovalRequests.

This is the single place where the HITL contract is enforced: every graph
interrupt becomes an ApprovalRequest row; every decision resumes its thread
through one code path. Graph nodes never touch persistence.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from pydantic import BaseModel

from brokerops_api.db import ApprovalRepo
from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest

LISTING_TO_CONTRACT = "listing_to_contract"
TRANSACTION_COORDINATION = "transaction_coordination"
VAPI_FOLLOWUP = "vapi_followup"

# State channels surfaced to API callers when a run finishes.
OUTPUT_KEYS = (
    "planned_tasks",
    "fub_task_ids",
    "outcome",
    "reminder_task_ids",
    "escalated_task_ids",
    "planned_calls",
    "feedback_id",
    "note_id",
    "call_log_id",
    "hot_task_id",
)
# Stage/outcome values that mean "the run did what it set out to do".
COMPLETED_VALUES = {
    "published",
    "on_track",
    "reminders_sent",
    "escalated",
    "call_queued",
    "synced",
    "agent_notified",
}


class WorkflowRunResult(BaseModel):
    thread_id: str
    status: str
    approval: ApprovalRequest | None = None
    output: dict[str, Any] | None = None


class WorkflowEngine:
    def __init__(self, graphs: Mapping[str, Any], repo: ApprovalRepo) -> None:
        self._graphs = dict(graphs)
        self._repo = repo

    async def start(self, workflow: str, run_input: dict[str, Any]) -> WorkflowRunResult:
        return await self._run(workflow, uuid4().hex, run_input)

    async def decide(
        self, approval: ApprovalRequest, decision: ApprovalDecision
    ) -> WorkflowRunResult:
        await self._repo.mark_decided(
            approval.id, decision.decision, decision.decided_by, datetime.now(UTC)
        )
        resume_value = {
            "decision": decision.decision.value,
            "decided_by": decision.decided_by,
            "edited_payload": decision.edited_payload,
        }
        return await self._run(
            approval.workflow, approval.graph_thread_id, Command(resume=resume_value)
        )

    async def _run(self, workflow: str, thread_id: str, run_input: Any) -> WorkflowRunResult:
        graph = self._graphs[workflow]
        config = {"configurable": {"thread_id": thread_id}}
        result: dict[str, Any] = await graph.ainvoke(run_input, config)

        interrupts = result.get("__interrupt__", [])
        if interrupts:
            value: dict[str, Any] = interrupts[0].value
            approval = ApprovalRequest(
                id=uuid4().hex,
                workflow=workflow,
                graph_thread_id=thread_id,
                kind=str(value.get("kind", "unknown")),
                payload=value,
                created_at=datetime.now(UTC),
            )
            await self._repo.create(approval)
            return WorkflowRunResult(
                thread_id=thread_id, status="awaiting_approval", approval=approval
            )

        terminal = result.get("stage") or result.get("outcome")
        terminal_value = str(getattr(terminal, "value", terminal))
        status = "completed" if terminal_value in COMPLETED_VALUES else terminal_value
        output = {key: result[key] for key in OUTPUT_KEYS if key in result} or None
        return WorkflowRunResult(thread_id=thread_id, status=status, output=output)
