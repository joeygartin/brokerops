"""The Phase 2 gate in test form: a HITL pause must survive a process restart.

Requires Postgres (TEST_DATABASE_URL); runs in CI against a service container
and locally against the compose db. The two `postgres_checkpointer` blocks
simulate two different api processes sharing nothing but the database.
"""

import os
from uuid import uuid4

import pytest
from conftest import FakeFeedbackStore, FakeVoice, GraphFakeCRM, GraphFakeMLS
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_langgraph.checkpointer import postgres_checkpointer
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract
from brokerops_langgraph.graphs.vapi_followup import build_vapi_followup

HOT_TRANSCRIPT = (
    "We loved the kitchen and the backyard was perfect. "
    "We are ready to write an offer. Our budget is between four fifty and five twenty five."
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="restart-survival proof needs Postgres (set TEST_DATABASE_URL)",
)


async def test_hitl_round_trip_survives_process_restart() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    thread_id = uuid4().hex
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    # "Process 1": start the run, hit the approval gate, then tear everything down.
    async with postgres_checkpointer(database_url) as saver:
        graph = build_listing_to_contract(GraphFakeMLS(), GraphFakeCRM(), saver)
        result = await graph.ainvoke({"listing_key": "RM1001"}, config)
        assert result["__interrupt__"][0].value["kind"] == "approve_marketing"

    # "Process 2": brand-new saver and graph instances — only the DB is shared.
    async with postgres_checkpointer(database_url) as saver:
        crm = GraphFakeCRM()
        graph = build_listing_to_contract(GraphFakeMLS(), crm, saver)
        result = await graph.ainvoke(
            Command(resume={"decision": "approved", "decided_by": "restart-test"}), config
        )
        assert result["stage"] == "published"
        assert result["fub_task_ids"] == [task.id for task in crm.created_tasks]


async def test_pre_gate_crm_sync_does_not_replay_on_postgres_resume() -> None:
    # vapi_followup writes a note + call log in sync_crm BEFORE the hot-lead
    # gate. Against Postgres (unlike InMemorySaver) the resume was replaying
    # that node, double-writing to the live CRM on every approval. One crm
    # instance spans both invokes, mirroring the long-lived api process.
    database_url = os.environ["TEST_DATABASE_URL"]
    config: RunnableConfig = {"configurable": {"thread_id": uuid4().hex}}
    crm, store = GraphFakeCRM(), FakeFeedbackStore()

    async with postgres_checkpointer(database_url) as saver:
        graph = build_vapi_followup(FakeVoice(), crm, store, DeterministicExtractor(), saver)
        paused = await graph.ainvoke(
            {
                "call_id": uuid4().hex,
                "listing_key": "RM1001",
                "contact_id": "101",
                "transcript": HOT_TRANSCRIPT,
                "call_outcome": "customer-ended-call",
            },
            config,
        )
        assert paused["__interrupt__"][0].value["kind"] == "notify_agent"
        assert len(crm.notes) == 1 and len(crm.logged_calls) == 1

        result = await graph.ainvoke(
            Command(resume={"decision": "approved", "decided_by": "restart-test"}), config
        )
        assert result["outcome"] == "agent_notified"
        assert len(crm.notes) == 1, "sync_crm note replayed on resume"
        assert len(crm.logged_calls) == 1, "sync_crm call log replayed on resume"
