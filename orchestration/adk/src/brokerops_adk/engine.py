"""ADK-backed workflow engine.

This is the single place where the HITL contract meets ADK: every workflow
pause (a RequestInput interrupt) becomes an ApprovalRequest row; every
decision resumes its paused invocation through one code path. The session id
doubles as the approval's graph_thread_id, so the API schema and frontend
are identical under either orchestrator.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.genai import types

from brokerops_adk.sessions import build_session_service, close_session_service
from brokerops_adk.workflows.listing_to_contract import build_listing_to_contract
from brokerops_adk.workflows.transaction_coordination import build_transaction_coordination
from brokerops_adk.workflows.vapi_followup import build_vapi_followup
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

# Sessions are keyed (app_name, user_id, session_id); runs aren't per-user.
USER_ID = "brokerops"


class AdkWorkflowEngine:
    def __init__(
        self,
        runners: Mapping[str, Runner],
        sessions: BaseSessionService,
        repo: ApprovalRepo,
    ) -> None:
        self._runners = dict(runners)
        self._sessions = sessions
        self._repo = repo

    async def start(self, workflow: str, run_input: dict[str, Any]) -> WorkflowRunResult:
        session = await self._sessions.create_session(
            app_name=workflow, user_id=USER_ID, session_id=uuid4().hex, state=dict(run_input)
        )
        message = types.Content(role="user", parts=[types.Part(text="start")])
        return await self._run(workflow, session.id, new_message=message)

    async def decide(
        self, approval: ApprovalRequest, decision: ApprovalDecision
    ) -> WorkflowRunResult:
        await self._repo.mark_decided(
            approval.id, decision.decision, decision.decided_by, datetime.now(UTC)
        )
        session = await self._sessions.get_session(
            app_name=approval.workflow, user_id=USER_ID, session_id=approval.graph_thread_id
        )
        if session is None:
            raise LookupError(f"no session for thread {approval.graph_thread_id}")
        # The paused invocation and its interrupt id live in the event log —
        # recover them there instead of widening the ApprovalRequest schema.
        interrupt_event = next(
            (event for event in reversed(session.events) if event.long_running_tool_ids), None
        )
        if interrupt_event is None:
            raise LookupError(f"thread {approval.graph_thread_id} has no pending interrupt")
        interrupt_id = next(iter(interrupt_event.long_running_tool_ids or ()))
        resume_value = {
            "decision": decision.decision.value,
            "decided_by": decision.decided_by,
            "edited_payload": decision.edited_payload,
        }
        message = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=interrupt_id, name="adk_request_input", response=resume_value
                    )
                )
            ],
        )
        return await self._run(
            approval.workflow,
            approval.graph_thread_id,
            new_message=message,
            invocation_id=interrupt_event.invocation_id,
        )

    async def _run(
        self,
        workflow: str,
        session_id: str,
        *,
        new_message: types.Content,
        invocation_id: str | None = None,
    ) -> WorkflowRunResult:
        runner = self._runners[workflow]
        pending: dict[str, Any] | None = None
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if event.long_running_tool_ids:
                for call in event.get_function_calls():
                    if call.id in event.long_running_tool_ids:
                        args = call.args or {}
                        pending = dict(args.get("payload") or {})

        if pending is not None:
            approval = ApprovalRequest(
                id=uuid4().hex,
                workflow=workflow,
                graph_thread_id=session_id,
                kind=str(pending.get("kind", "unknown")),
                payload=pending,
                created_at=datetime.now(UTC),
            )
            await self._repo.create(approval)
            return WorkflowRunResult(
                thread_id=session_id, status="awaiting_approval", approval=approval
            )

        session = await self._sessions.get_session(
            app_name=workflow, user_id=USER_ID, session_id=session_id
        )
        assert session is not None
        return terminal_run_result(session_id, dict(session.state))


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
) -> AsyncIterator[AdkWorkflowEngine]:
    """Wire the three workflows to a session service and yield a ready engine.

    With a database URL, ADK sessions share Postgres with the approvals so
    HITL pauses survive restarts and deploys; without one, everything lives
    in memory and nothing survives. No workflow uses an LlmAgent, so the
    engine needs no model credentials — demo mode stays zero-credential.
    """
    sessions = build_session_service(database_url)
    workflows = {
        LISTING_TO_CONTRACT: build_listing_to_contract(mls, crm),
        TRANSACTION_COORDINATION: build_transaction_coordination(transaction_store, crm),
        VAPI_FOLLOWUP: build_vapi_followup(voice, crm, feedback_store),
    }
    runners = {
        name: Runner(
            app=App(
                name=name,
                root_agent=wf,
                resumability_config=ResumabilityConfig(is_resumable=True),
            ),
            session_service=sessions,
        )
        for name, wf in workflows.items()
    }
    try:
        yield AdkWorkflowEngine(runners, sessions, approval_repo)
    finally:
        await close_session_service(sessions)
