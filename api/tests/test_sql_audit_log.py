"""SqlAuditLog against in-memory SQLite — portable table behavior + restart survival.

The trail is committed rows, so a fresh SqlAuditLog over the same engine (a stand-in
for a process restart) still sees every prior record — the durability the ledger needs.
"""

from datetime import UTC, datetime, timedelta
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
    assert rows[0].args is not None
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


def _tagged(*, run: str, created_at: datetime, transaction_id: str = "") -> MutationRecord:
    return MutationRecord(
        id=uuid4().hex,
        workflow_run_id=run,
        workflow="transaction_coordination",
        transaction_id=transaction_id,
        tool="create_task",
        integration="followupboss",
        args={"name": "reminder"},
        outcome=MutationOutcome.SUCCESS,
        created_at=created_at,
    )


async def test_transaction_slice_finds_matches_beyond_any_scan_window(
    engine: AsyncEngine,
) -> None:
    # BOP-027 review r1 regression: the slice must not scan a bounded newest-N
    # window and drop older matches. Bury one matching record under 1100 NEWER
    # unrelated ones — well past the retired 1000-row scan cap — and it must surface.
    audit = SqlAuditLog(engine)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await audit.record(_tagged(run="txn-run", created_at=base, transaction_id="TXN-42"))
    for i in range(1100):
        await audit.record(_tagged(run=f"noise-{i}", created_at=base + timedelta(minutes=i + 10)))

    slice_ = await audit.list_for_transaction("TXN-42", limit=200)
    assert [r.workflow_run_id for r in slice_] == ["txn-run"]  # found; noise excluded


async def test_transaction_slice_finds_writes_from_runs_with_no_approval(
    engine: AsyncEngine,
) -> None:
    # BOP-027 review r2 regression: completeness must not depend on an approval
    # existing for the run. A transaction-scoped write stamped at record time is
    # matched by its own transaction_id, even with no ApprovalRequest anywhere.
    audit = SqlAuditLog(engine)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await audit.record(_tagged(run="reminder-run", created_at=base, transaction_id="TXN-9"))
    await audit.record(_tagged(run="other-deal", created_at=base, transaction_id="TXN-OTHER"))
    await audit.record(_tagged(run="unscoped", created_at=base))  # transaction_id=""

    slice_ = await audit.list_for_transaction("TXN-9")
    assert [r.workflow_run_id for r in slice_] == ["reminder-run"]
