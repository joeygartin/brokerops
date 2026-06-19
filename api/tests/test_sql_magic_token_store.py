"""SqlMagicTokenStore against in-memory SQLite — atomic single-use consume."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlMagicTokenStore, metadata


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


async def test_create_then_consume_returns_row(engine: AsyncEngine) -> None:
    store = SqlMagicTokenStore(engine)
    expires = datetime.now(UTC) + timedelta(minutes=15)
    await store.create("hash-abc", "op@acme.com", expires)
    consumed = await store.consume("hash-abc")
    assert consumed is not None
    assert consumed.email == "op@acme.com"


async def test_consume_is_single_use(engine: AsyncEngine) -> None:
    store = SqlMagicTokenStore(engine)
    await store.create("hash-xyz", "op@acme.com", datetime.now(UTC) + timedelta(minutes=15))
    assert await store.consume("hash-xyz") is not None
    assert await store.consume("hash-xyz") is None  # second claim finds no unused row


async def test_consume_unknown_returns_none(engine: AsyncEngine) -> None:
    store = SqlMagicTokenStore(engine)
    assert await store.consume("never-created") is None
