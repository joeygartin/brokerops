"""Approval persistence — the ApprovalRequest table and its repositories.

`SqlApprovalRepo` is the real store (Postgres; the table is migrated by
Alembic). `InMemoryApprovalRepo` backs tests and database-less local dev.
"""

from datetime import datetime
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus

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
