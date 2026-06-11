"""Workflow engine — runs graphs and translates interrupts into ApprovalRequests.

This is the single place where the HITL contract is enforced: every graph
interrupt becomes an ApprovalRequest row; every decision resumes its thread
through one code path. Graph nodes never touch persistence.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from pydantic import BaseModel

from brokerops_api.db import ApprovalRepo
from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest

LISTING_TO_CONTRACT = "listing_to_contract"


class WorkflowRunResult(BaseModel):
    thread_id: str
    status: str
    approval: ApprovalRequest | None = None
    output: dict[str, Any] | None = None


class WorkflowEngine:
    def __init__(self, graph: Any, repo: ApprovalRepo) -> None:
        self._graph = graph
        self._repo = repo

    async def start_listing_to_contract(self, listing_key: str) -> WorkflowRunResult:
        thread_id = uuid4().hex
        return await self._run(thread_id, {"listing_key": listing_key})

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
        return await self._run(approval.graph_thread_id, Command(resume=resume_value))

    async def _run(self, thread_id: str, run_input: Any) -> WorkflowRunResult:
        config = {"configurable": {"thread_id": thread_id}}
        result: dict[str, Any] = await self._graph.ainvoke(run_input, config)

        interrupts = result.get("__interrupt__", [])
        if interrupts:
            value: dict[str, Any] = interrupts[0].value
            approval = ApprovalRequest(
                id=uuid4().hex,
                workflow=LISTING_TO_CONTRACT,
                graph_thread_id=thread_id,
                kind=str(value.get("kind", "unknown")),
                payload=value,
                created_at=datetime.now(UTC),
            )
            await self._repo.create(approval)
            return WorkflowRunResult(
                thread_id=thread_id, status="awaiting_approval", approval=approval
            )

        stage = result.get("stage")
        stage_value = getattr(stage, "value", stage)
        status = "completed" if stage_value == "published" else str(stage_value)
        output = {
            key: result[key] for key in ("planned_tasks", "fub_task_ids") if key in result
        } or None
        return WorkflowRunResult(thread_id=thread_id, status=status, output=output)
