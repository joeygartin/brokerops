"""SqlMessageStore against in-memory SQLite — upsert lifecycle + restart survival.

Like the audit-ledger's SQL tests: portable table behavior over the shared metadata,
and a fresh store over the same engine (a stand-in for a process restart) still sees
the comms history.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlMessageStore, metadata
from brokerops_core.models.message import Message, MessageChannel, MessageStatus


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


def _message(message_id: str = "m-1", contact_id: str = "101") -> Message:
    return Message(
        id=message_id,
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject="Following up",
        body="Hi Sam",
        template_ref="showing_followup:v1",
        contact_id=contact_id,
        listing_key="RM1001",
        status=MessageStatus.DRAFTED,
        created_at=datetime.now(UTC),
    )


async def test_save_and_get_roundtrip(engine: AsyncEngine) -> None:
    store = SqlMessageStore(engine)
    await store.save_message(_message())
    row = await store.get_message("m-1")
    assert row is not None
    assert row.recipient == "sam@example.com"
    assert row.status is MessageStatus.DRAFTED
    assert await store.get_message("nope") is None


async def test_save_is_an_upsert_across_the_lifecycle(engine: AsyncEngine) -> None:
    store = SqlMessageStore(engine)
    drafted = _message()
    await store.save_message(drafted)
    sent = drafted.model_copy(
        update={
            "status": MessageStatus.SENT,
            "provider_message_id": "stub-email-9001",
            "sent_at": datetime.now(UTC),
        }
    )
    await store.save_message(sent)
    rows = await store.list_messages()
    assert len(rows) == 1  # one row per message id, not one per save
    assert rows[0].status is MessageStatus.SENT
    assert rows[0].provider_message_id == "stub-email-9001"


async def test_list_filters_by_contact_and_bounds_limit(engine: AsyncEngine) -> None:
    store = SqlMessageStore(engine)
    await store.save_message(_message("m-1", contact_id="101"))
    await store.save_message(_message("m-2", contact_id="102"))
    await store.save_message(_message("m-3", contact_id="101"))
    only_101 = await store.list_messages(contact_id="101")
    assert {m.id for m in only_101} == {"m-1", "m-3"}
    assert len(await store.list_messages(limit=1)) == 1


async def test_history_survives_a_restart(engine: AsyncEngine) -> None:
    await SqlMessageStore(engine).save_message(_message())
    survived = await SqlMessageStore(engine).get_message("m-1")
    assert survived is not None
    assert survived.template_ref == "showing_followup:v1"


# ── advance_message_status: the conditional forward-only transition (BOP-037) ──


async def test_advance_moves_the_lifecycle_forward_and_returns_the_row(
    engine: AsyncEngine,
) -> None:
    store = SqlMessageStore(engine)
    await store.save_message(_message().model_copy(update={"status": MessageStatus.SENT}))
    row = await store.advance_message_status("m-1", MessageStatus.DELIVERED)
    assert row is not None and row.status is MessageStatus.DELIVERED
    fetched = await store.get_message("m-1")
    assert fetched is not None and fetched.status is MessageStatus.DELIVERED


async def test_advance_refuses_downgrades_and_rank_equal_transitions(
    engine: AsyncEngine,
) -> None:
    store = SqlMessageStore(engine)
    await store.save_message(_message().model_copy(update={"status": MessageStatus.DELIVERED}))
    # a late "sent" (lower rank) and a "failed" (equal rank) both leave the row alone
    late = await store.advance_message_status("m-1", MessageStatus.SENT)
    assert late is not None and late.status is MessageStatus.DELIVERED
    equal = await store.advance_message_status("m-1", MessageStatus.FAILED)
    assert equal is not None and equal.status is MessageStatus.DELIVERED


async def test_advance_on_a_missing_row_returns_none(engine: AsyncEngine) -> None:
    assert await SqlMessageStore(engine).advance_message_status("nope", MessageStatus.SENT) is None


async def test_advance_writes_only_the_status_column(engine: AsyncEngine) -> None:
    # The BOP-037 race fix: the old webhook path saved a FULL row snapshot, so a
    # callback racing another writer could clobber concurrent field changes.
    # The conditional transition touches only status — an edit that landed after
    # the callback's read survives.
    store = SqlMessageStore(engine)
    await store.save_message(_message().model_copy(update={"status": MessageStatus.SENT}))
    stale_read = await store.get_message("m-1")
    assert stale_read is not None
    # another writer updates the body after the callback's read
    await store.save_message(stale_read.model_copy(update={"body": "edited concurrently"}))
    advanced = await store.advance_message_status("m-1", MessageStatus.DELIVERED)
    assert advanced is not None
    assert advanced.status is MessageStatus.DELIVERED
    assert advanced.body == "edited concurrently"  # not clobbered back to "Hi Sam"


async def test_two_racing_callbacks_produce_exactly_one_forward_transition(
    engine: AsyncEngine,
) -> None:
    # Two delivery callbacks for one sid land concurrently (Twilio guarantees no
    # ordering): both target rank-3 statuses, so exactly one may win — the loser
    # must observe the winner's status, never overwrite it (the old
    # read-rank-guard-then-save let both pass the guard; last writer won).
    import asyncio

    store = SqlMessageStore(engine)
    await store.save_message(_message().model_copy(update={"status": MessageStatus.SENT}))
    delivered, failed = await asyncio.gather(
        store.advance_message_status("m-1", MessageStatus.DELIVERED),
        store.advance_message_status("m-1", MessageStatus.FAILED),
    )
    assert delivered is not None and failed is not None
    final = await store.get_message("m-1")
    assert final is not None
    assert final.status in (MessageStatus.DELIVERED, MessageStatus.FAILED)
    # both calls converged on the same final row — no second transition happened
    assert delivered.status is final.status
    assert failed.status is final.status
