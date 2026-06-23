"""create_transaction through the domain TransactionStore port (SQL + in-memory).

BOP-004 step 1: a transaction opened via the domain write path must be visible to
the same reads transaction_coordination uses.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from brokerops_api.db import InMemoryTransactionStore, SqlTransactionStore, metadata
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.ports.transactions import TransactionAlreadyExists, TransactionStore


def _sample() -> tuple[Transaction, list[Milestone]]:
    today = date.today()
    txn = Transaction(
        id="TXN-9001",
        listing_key="RM-9001",
        stage=TransactionStage.UNDER_CONTRACT,
        parties=[TransactionParty(role="buyer", name="Sam Rivera", contact_id="201")],
        contract_date=today,
        close_date=today + timedelta(days=30),
    )
    milestones = [
        Milestone(
            id="MS-9001-CLO",
            transaction_id=txn.id,
            type=MilestoneType.CLOSING,
            title="Closing",
            due_date=today + timedelta(days=30),
            owner="Escrow",
        ),
        Milestone(
            id="MS-9001-INS",
            transaction_id=txn.id,
            type=MilestoneType.INSPECTION,
            title="Home inspection",
            due_date=today + timedelta(days=10),
            owner="Listing agent",
        ),
    ]
    return txn, milestones


@pytest.fixture(params=["sql", "memory"])
async def store(request: pytest.FixtureRequest) -> TransactionStore:
    if request.param == "memory":
        return InMemoryTransactionStore()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return SqlTransactionStore(engine)


async def test_create_transaction_is_visible_through_reads(store: TransactionStore) -> None:
    txn, milestones = _sample()
    await store.create_transaction(txn, milestones)

    fetched = await store.get_transaction(txn.id)
    assert fetched is not None
    assert fetched.listing_key == "RM-9001"

    active = await store.list_active_transactions()
    assert [t.id for t in active] == [txn.id]

    # Both stores return milestones ordered by due_date.
    stored = await store.list_milestones(txn.id)
    assert [m.id for m in stored] == ["MS-9001-INS", "MS-9001-CLO"]


async def test_create_transaction_duplicate_id_raises(store: TransactionStore) -> None:
    # The atomic claim behind idempotent open: a second create with the same id is
    # rejected (SQLite enforces the primary key) — the route catches this on a race.
    txn, milestones = _sample()
    await store.create_transaction(txn, milestones)
    with pytest.raises(TransactionAlreadyExists):
        await store.create_transaction(txn, milestones)
