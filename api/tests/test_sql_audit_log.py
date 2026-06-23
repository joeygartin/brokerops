"""SqlAuditLog against in-memory SQLite — portable table behavior + restart survival.

The trail is committed rows, so a fresh SqlAuditLog over the same engine (a stand-in
for a process restart) still sees every prior record — the durability the ledger needs.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlAuditLog, metadata
from brokerops_core.models.mutation import MutationOutcome, MutationRecord


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


def _record(run: str = "run-1", tool: str = "create_task") -> MutationRecord:
    return MutationRecord(
        id=uuid4().hex,
        workflow_run_id=run,
        workflow="listing_to_contract",
        tool=tool,
        integration="followupboss",
        args={"name": "Order signage"},
        approval_id="ap-1",
        actor="joey@x",
        outcome=MutationOutcome.SUCCESS,
        external_id="task-9",
        created_at=datetime.now(UTC),
    )


async def test_record_and_list_roundtrip(engine: AsyncEngine) -> None:
    audit = SqlAuditLog(engine)
    await audit.record(_record())
    rows = await audit.list()
    assert len(rows) == 1
    assert rows[0].tool == "create_task"
    assert rows[0].args["name"] == "Order signage"
    assert rows[0].approval_id == "ap-1"


async def test_list_filters_by_run(engine: AsyncEngine) -> None:
    audit = SqlAuditLog(engine)
    await audit.record(_record(run="run-a"))
    await audit.record(_record(run="run-b", tool="add_note"))
    only_a = await audit.list(workflow_run_id="run-a")
    assert [r.tool for r in only_a] == ["create_task"]


async def test_trail_survives_a_restart(engine: AsyncEngine) -> None:
    await SqlAuditLog(engine).record(_record())
    # Fresh instance over the same database = the post-restart process.
    survived = await SqlAuditLog(engine).list()
    assert len(survived) == 1
    assert survived[0].external_id == "task-9"
