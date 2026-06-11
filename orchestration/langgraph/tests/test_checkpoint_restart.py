"""The Phase 2 gate in test form: a HITL pause must survive a process restart.

Requires Postgres (TEST_DATABASE_URL); runs in CI against a service container
and locally against the compose db. The two `postgres_checkpointer` blocks
simulate two different api processes sharing nothing but the database.
"""

import os
from uuid import uuid4

import pytest
from conftest import GraphFakeCRM, GraphFakeMLS
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from brokerops_langgraph.checkpointer import postgres_checkpointer
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract

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
