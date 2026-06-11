"""Domain persistence — approval, transaction, and milestone tables + repositories.

`Sql*` classes are the real stores (Postgres; tables migrated by Alembic).
`InMemory*` twins back tests and database-less local dev.
"""

from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.transaction import ACTIVE_STAGES, Transaction

metadata = sa.MetaData()

approval_requests = sa.Table(
    "approval_requests",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("workflow", sa.String(64), nullable=False),
    sa.Column("graph_thread_id", sa.String(64), nullable=False, index=True),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
    sa.Column("decided_by", sa.String(120)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("decided_at", sa.DateTime(timezone=True)),
)


transactions = sa.Table(
    "transactions",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("listing_key", sa.String(36), nullable=False, index=True),
    sa.Column("stage", sa.String(32), nullable=False, index=True),
    sa.Column("parties", sa.JSON(), nullable=False),
    sa.Column("contract_date", sa.Date(), nullable=False),
    sa.Column("close_date", sa.Date()),
)

milestones = sa.Table(
    "milestones",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
    sa.Column("type", sa.String(32), nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("due_date", sa.Date(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("owner", sa.String(120), nullable=False, server_default=""),
    sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("blocked_reason", sa.String(300)),
)


def sqlalchemy_url(database_url: str) -> str:
    """Normalize a plain postgres DSN to SQLAlchemy's psycopg dialect."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(sqlalchemy_url(database_url))


class ApprovalRepo(Protocol):
    async def create(self, approval: ApprovalRequest) -> None: ...

    async def get(self, approval_id: str) -> ApprovalRequest | None: ...

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]: ...

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None: ...


def _row_to_model(row: Row[tuple[object, ...]]) -> ApprovalRequest:
    return ApprovalRequest.model_validate(dict(row._mapping))


class SqlApprovalRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, approval: ApprovalRequest) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(approval_requests.insert().values(**approval.model_dump()))

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                approval_requests.select().where(approval_requests.c.id == approval_id)
            )
            row = result.first()
        return _row_to_model(row) if row is not None else None

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        query = approval_requests.select().order_by(approval_requests.c.created_at.desc())
        if status is not None:
            query = query.where(approval_requests.c.status == status.value)
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            rows = result.all()
        return [_row_to_model(row) for row in rows]

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                approval_requests.update()
                .where(approval_requests.c.id == approval_id)
                .values(status=status.value, decided_by=decided_by, decided_at=decided_at)
            )


class TransactionStoreAdmin(Protocol):
    """Seeding/reset surface used by the demo controls (not by workflows)."""

    async def count_transactions(self) -> int: ...

    async def insert(self, transaction: Transaction, txn_milestones: list[Milestone]) -> None: ...

    async def clear(self) -> None: ...


def _txn_to_row(transaction: Transaction) -> dict[str, Any]:
    row = transaction.model_dump(mode="json")
    row["contract_date"] = transaction.contract_date
    row["close_date"] = transaction.close_date
    return row


def _milestone_to_row(milestone: Milestone) -> dict[str, Any]:
    row = milestone.model_dump(mode="json")
    row["due_date"] = milestone.due_date
    return row


class SqlTransactionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                transactions.select().where(transactions.c.id == transaction_id)
            )
            row = result.first()
        return Transaction.model_validate(dict(row._mapping)) if row is not None else None

    async def list_active_transactions(self) -> list[Transaction]:
        stages = [stage.value for stage in ACTIVE_STAGES]
        async with self._engine.connect() as conn:
            result = await conn.execute(
                transactions.select().where(transactions.c.stage.in_(stages))
            )
            rows = result.all()
        return [Transaction.model_validate(dict(row._mapping)) for row in rows]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                milestones.select()
                .where(milestones.c.transaction_id == transaction_id)
                .order_by(milestones.c.due_date)
            )
            rows = result.all()
        return [Milestone.model_validate(dict(row._mapping)) for row in rows]

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                milestones.update()
                .where(milestones.c.id == milestone_id)
                .values(escalation_level=level)
            )

    async def count_transactions(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(sa.select(sa.func.count()).select_from(transactions))
        return int(result.scalar_one())

    async def insert(self, transaction: Transaction, txn_milestones: list[Milestone]) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(transactions.insert().values(**_txn_to_row(transaction)))
            for milestone in txn_milestones:
                await conn.execute(milestones.insert().values(**_milestone_to_row(milestone)))

    async def clear(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(milestones.delete())
            await conn.execute(transactions.delete())


class InMemoryTransactionStore:
    def __init__(self) -> None:
        self._transactions: dict[str, Transaction] = {}
        self._milestones: dict[str, Milestone] = {}

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._transactions.get(transaction_id)

    async def list_active_transactions(self) -> list[Transaction]:
        return [t for t in self._transactions.values() if t.is_active]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        found = [m for m in self._milestones.values() if m.transaction_id == transaction_id]
        return sorted(found, key=lambda m: m.due_date)

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        existing = self._milestones[milestone_id]
        self._milestones[milestone_id] = existing.model_copy(update={"escalation_level": level})

    async def count_transactions(self) -> int:
        return len(self._transactions)

    async def insert(self, transaction: Transaction, txn_milestones: list[Milestone]) -> None:
        self._transactions[transaction.id] = transaction
        for milestone in txn_milestones:
            self._milestones[milestone.id] = milestone

    async def clear(self) -> None:
        self._transactions.clear()
        self._milestones.clear()


class InMemoryApprovalRepo:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}

    async def create(self, approval: ApprovalRequest) -> None:
        self._items[approval.id] = approval

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._items.get(approval_id)

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        items = sorted(self._items.values(), key=lambda a: a.created_at, reverse=True)
        return [a for a in items if status is None or a.status is status]

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None:
        existing = self._items[approval_id]
        self._items[approval_id] = existing.model_copy(
            update={"status": status, "decided_by": decided_by, "decided_at": decided_at}
        )
