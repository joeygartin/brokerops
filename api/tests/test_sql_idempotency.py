"""SqlIdempotencyStore against in-memory SQLite — atomic claim + restart survival.

The claim is committed rows, so a fresh store over the same engine (a stand-in for a
process restart) sees the prior completed claim and answers a replay from it — the
durability that makes the "kill mid-write, replay, exactly one effect" proof hold.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlIdempotencyStore, metadata
from brokerops_core.models.idempotency import ClaimStatus


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


async def test_first_claim_is_new(engine: AsyncEngine) -> None:
    store = SqlIdempotencyStore(engine)
    claim = await store.begin("k1", workflow_run_id="run-1", tool="create_task")
    assert claim.status is ClaimStatus.NEW
    assert claim.result is None


async def test_second_claim_before_completion_is_pending(engine: AsyncEngine) -> None:
    store = SqlIdempotencyStore(engine)
    await store.begin("k1", workflow_run_id="run-1", tool="create_task")
    again = await store.begin("k1", workflow_run_id="run-1", tool="create_task")
    assert again.status is ClaimStatus.PENDING
    assert again.result is None


async def test_completed_claim_returns_stored_result(engine: AsyncEngine) -> None:
    store = SqlIdempotencyStore(engine)
    await store.begin("k1", workflow_run_id="run-1", tool="add_note")
    await store.complete("k1", "note-9")
    replay = await store.begin("k1", workflow_run_id="run-1", tool="add_note")
    assert replay.status is ClaimStatus.COMPLETED
    assert replay.result == "note-9"


async def test_completed_claim_survives_a_restart(engine: AsyncEngine) -> None:
    await SqlIdempotencyStore(engine).begin("k1", workflow_run_id="run-1", tool="add_note")
    await SqlIdempotencyStore(engine).complete("k1", "note-9")
    # Fresh instance over the same database = the post-restart process.
    survived = await SqlIdempotencyStore(engine).begin(
        "k1", workflow_run_id="run-1", tool="add_note"
    )
    assert survived.status is ClaimStatus.COMPLETED
    assert survived.result == "note-9"
