"""The Phase 2 gate in ADK form: a HITL pause must survive a process restart.

Requires Postgres (TEST_DATABASE_URL); runs in CI against a service container
and locally against the compose db. The two engines share nothing but the
database — separate session services, runners, and workflow instances
simulate two different api processes.
"""

import os
from uuid import uuid4

import pytest
from workflow_fixtures import (
    FakeApprovalRepo,
    FakeFeedbackStore,
    FakeVoice,
    GraphFakeCRM,
    GraphFakeMLS,
    final_state,
    make_message_service,
)
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService

from brokerops_adk.engine import AdkWorkflowEngine
from brokerops_adk.sessions import build_session_service, close_session_service
from brokerops_adk.workflows.listing_to_contract import build_listing_to_contract
from brokerops_adk.workflows.vapi_followup import build_vapi_followup
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.contact import Contact
from brokerops_core.models.message import MessageStatus
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.message_send import MessageSendService
from brokerops_core.services.workflow_runs import LISTING_TO_CONTRACT, VAPI_FOLLOWUP

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


def _followup_process(
    database_url: str, messages: MessageSendService
) -> tuple[AdkWorkflowEngine, BaseSessionService]:
    sessions = build_session_service(database_url)
    crm = GraphFakeCRM()
    crm.contacts["101"] = Contact(crm_id="101", name="Jordan Pike", email="jp@example.test")
    workflow = build_vapi_followup(
        FakeVoice(), crm, FakeFeedbackStore(), DeterministicExtractor(), messages
    )
    runner = Runner(
        app=App(
            name=workflow.name,
            root_agent=workflow,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=sessions,
    )
    return AdkWorkflowEngine({VAPI_FOLLOWUP: runner}, sessions, FakeApprovalRepo()), sessions


async def test_outbound_message_gate_survives_process_restart() -> None:
    # BOP-019: a run paused at the approve-outbound-message gate must resume in
    # a brand-new "process" and send exactly once — with the approver's edited
    # text. The message store spans both processes like the durable stores of
    # the real api; the workflow session itself lives only in the shared DB.
    database_url = os.environ["TEST_DATABASE_URL"]
    messages, email, message_store = make_message_service()

    # "Process 1": pause at the drafted-follow-up gate, then tear down.
    engine1, sessions1 = _followup_process(database_url, messages)
    run = await engine1.start(
        VAPI_FOLLOWUP,
        {
            "call_id": uuid4().hex,
            "listing_key": "RM1001",
            "contact_id": "101",
            "transcript": "Nice house but it felt overpriced. We will keep looking.",
            "call_outcome": "customer-ended-call",
        },
    )
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    assert run.approval.kind == "approve_outbound_message"
    message_id = str(run.approval.payload["message_id"])
    assert email.sent == []
    await close_session_service(sessions1)

    # "Process 2": brand-new service, runner, engine — resume with edited text.
    engine2, sessions2 = _followup_process(database_url, messages)
    result = await engine2.decide(
        run.approval,
        ApprovalDecision(
            decision=ApprovalStatus.APPROVED,
            decided_by="restart-test",
            edited_payload={"body": "Edited across the restart."},
        ),
    )
    assert result.status == "completed"
    state = await final_state(sessions2, VAPI_FOLLOWUP, run.thread_id)
    assert state["outcome"] == "followup_sent"
    assert [m.body for m in email.sent] == ["Edited across the restart."]
    row = message_store.rows[message_id]
    assert row.status is MessageStatus.SENT
    assert row.body == "Edited across the restart."
    await close_session_service(sessions2)
