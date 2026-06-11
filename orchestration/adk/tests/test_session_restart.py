"""The Phase 2 gate in ADK form: a HITL pause must survive a process restart.

Requires Postgres (TEST_DATABASE_URL); runs in CI against a service container
and locally against the compose db. The two engines share nothing but the
database — separate session services, runners, and workflow instances
simulate two different api processes.
"""

import os

import pytest
from workflow_fixtures import FakeApprovalRepo, GraphFakeCRM, GraphFakeMLS, final_state
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService

from brokerops_adk.engine import AdkWorkflowEngine
from brokerops_adk.sessions import build_session_service, close_session_service
from brokerops_adk.workflows.listing_to_contract import build_listing_to_contract
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.services.workflow_runs import LISTING_TO_CONTRACT

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="restart-survival proof needs Postgres (set TEST_DATABASE_URL)",
)


def _process(database_url: str, crm: GraphFakeCRM) -> tuple[AdkWorkflowEngine, BaseSessionService]:
    """Everything a fresh api process would build: service, runner, engine."""
    sessions = build_session_service(database_url)
    workflow = build_listing_to_contract(GraphFakeMLS(), crm)
    runner = Runner(
        app=App(
            name=workflow.name,
            root_agent=workflow,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=sessions,
    )
    return AdkWorkflowEngine({LISTING_TO_CONTRACT: runner}, sessions, FakeApprovalRepo()), sessions


async def test_hitl_round_trip_survives_process_restart() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]

    # "Process 1": start the run, hit the approval gate, then tear everything down.
    engine1, sessions1 = _process(database_url, GraphFakeCRM())
    run = await engine1.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    assert run.approval.kind == "approve_marketing"
    await close_session_service(sessions1)

    # "Process 2": brand-new service, runner, and engine — only the DB is shared.
    crm = GraphFakeCRM()
    engine2, sessions2 = _process(database_url, crm)
    result = await engine2.decide(
        run.approval,
        ApprovalDecision(decision=ApprovalStatus.APPROVED, decided_by="restart-test"),
    )
    assert result.status == "completed"
    state = await final_state(sessions2, LISTING_TO_CONTRACT, run.thread_id)
    assert state["stage"] == "published"
    assert state["fub_task_ids"] == [task.id for task in crm.created_tasks]
    await close_session_service(sessions2)
