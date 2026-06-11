"""LangGraph-backed workflow engine.

This is the single place where the HITL contract meets LangGraph: every
graph interrupt becomes an ApprovalRequest row; every decision resumes its
thread through one code path. Graph nodes never touch persistence.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest
from brokerops_core.ports.approvals import ApprovalRepo
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.mls import MLSPort
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.workflow_runs import (
    LISTING_TO_CONTRACT,
    TRANSACTION_COORDINATION,
    VAPI_FOLLOWUP,
    WorkflowRunResult,
    terminal_run_result,
)
from brokerops_langgraph.checkpointer import postgres_checkpointer
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract
from brokerops_langgraph.graphs.transaction_coordination import build_transaction_coordination
from brokerops_langgraph.graphs.vapi_followup import build_vapi_followup


class LangGraphWorkflowEngine:
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

        return terminal_run_result(thread_id, result)


@asynccontextmanager
async def build_engine(
    *,
    mls: MLSPort,
    crm: CRMPort,
    voice: VoicePort,
    transaction_store: TransactionStore,
    feedback_store: FeedbackStore,
    approval_repo: ApprovalRepo,
    database_url: str | None,
) -> AsyncIterator[LangGraphWorkflowEngine]:
    """Wire the three graphs to a checkpointer and yield a ready engine.

    With a database URL, graph state checkpoints share Postgres with the
    approvals so HITL pauses survive restarts and deploys; without one,
    everything lives in memory and nothing survives.
    """
    if database_url:
        async with postgres_checkpointer(database_url) as saver:
            yield _engine(mls, crm, voice, transaction_store, feedback_store, approval_repo, saver)
    else:
        yield _engine(
            mls, crm, voice, transaction_store, feedback_store, approval_repo, InMemorySaver()
        )


def _engine(
    mls: MLSPort,
    crm: CRMPort,
    voice: VoicePort,
    transaction_store: TransactionStore,
    feedback_store: FeedbackStore,
    approval_repo: ApprovalRepo,
    saver: Any,
) -> LangGraphWorkflowEngine:
    graphs = {
        LISTING_TO_CONTRACT: build_listing_to_contract(mls, crm, saver),
        TRANSACTION_COORDINATION: build_transaction_coordination(transaction_store, crm, saver),
        VAPI_FOLLOWUP: build_vapi_followup(voice, crm, feedback_store, saver),
    }
    return LangGraphWorkflowEngine(graphs, approval_repo)
