"""Domain persistence — approval, transaction, and milestone tables + repositories.

`Sql*` classes are the real stores (Postgres; tables migrated by Alembic).
`InMemory*` twins back tests and database-less local dev.
"""

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from brokerops_core.services.tenancy import require_tenant

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.ports.approvals import ApprovalRepo as ApprovalRepo
from brokerops_core.ports.audit import AuditLog as AuditLog
from brokerops_core.ports.auth import ConsumedToken
from brokerops_core.ports.idempotency import IdempotencyStore as IdempotencyStore
from brokerops_core.models.document import Document
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.models.message import STATUS_RANK, Message, MessageStatus
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.models.transaction import ACTIVE_STAGES, Transaction
from brokerops_core.ports.documents import DocumentAlreadyExists
from brokerops_core.ports.transactions import TransactionAlreadyExists

metadata = sa.MetaData()

# Every agent-reachable table carries tenant_id (BOP-006). The authoritative column
# DDL — the GUC-derived default and the row-level-security policy — lives in migration
# 0007; the server_default="" on the Table objects below only keeps them valid for
# query building (db.py does not run DDL itself).

approval_requests = sa.Table(
    "approval_requests",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
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
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
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
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
    sa.Column("type", sa.String(32), nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("due_date", sa.Date(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("owner", sa.String(120), nullable=False, server_default=""),
    sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("blocked_reason", sa.String(300)),
    sa.Column("expected_document", sa.String(32)),
)


documents = sa.Table(
    "documents",
    metadata,
    # Metadata/pointers only (BOP-021): the FileRef JSON points into the office
    # file store; no file bytes are ever stored here.
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
    sa.Column("milestone_id", sa.String(64)),
    sa.Column("kind", sa.String(32), nullable=False, server_default="other"),
    sa.Column("title", sa.String(300), nullable=False, server_default=""),
    sa.Column("file", sa.JSON(), nullable=False),
    sa.Column("uploaded_by", sa.String(254), nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)


call_records = sa.Table(
    "call_records",
    metadata,
    sa.Column("vapi_call_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("contact_id", sa.String(36), nullable=False, server_default=""),
    sa.Column("listing_key", sa.String(36), nullable=False, server_default="", index=True),
    sa.Column("transcript", sa.Text(), nullable=False, server_default=""),
    sa.Column("outcome", sa.String(64), nullable=False, server_default=""),
    sa.Column("extracted", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

showing_feedback = sa.Table(
    "showing_feedback",
    metadata,
    sa.Column("id", sa.String(80), primary_key=True),
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("listing_key", sa.String(36), nullable=False, index=True),
    sa.Column("contact_id", sa.String(36), nullable=False),
    sa.Column("call_id", sa.String(64)),
    sa.Column("source", sa.String(16), nullable=False, server_default="call"),
    sa.Column("sentiment", sa.String(16), nullable=False, server_default="neutral"),
    sa.Column("structured_answers", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)


outbound_messages = sa.Table(
    "outbound_messages",
    metadata,
    # The outbound comms history (BOP-015): one row per message, all channels.
    # Domain data like call_records — the audit ledger separately records the send
    # crossing the provider boundary. Authoritative DDL (GUC default + RLS) is in
    # migration 0008; server_default="" here only keeps query building valid.
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("channel", sa.String(16), nullable=False, server_default="email"),
    sa.Column("recipient", sa.String(254), nullable=False),
    sa.Column("subject", sa.String(500), nullable=False, server_default=""),
    sa.Column("body", sa.Text(), nullable=False, server_default=""),
    sa.Column("template_ref", sa.String(120), nullable=False, server_default=""),
    sa.Column("contact_id", sa.String(36), nullable=False, server_default="", index=True),
    sa.Column("listing_key", sa.String(36), nullable=False, server_default="", index=True),
    sa.Column("transaction_id", sa.String(36), nullable=False, server_default=""),
    sa.Column("status", sa.String(24), nullable=False, server_default="drafted"),
    sa.Column("provider_message_id", sa.String(120), nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("sent_at", sa.DateTime(timezone=True)),
)


magic_login_tokens = sa.Table(
    "magic_login_tokens",
    metadata,
    # Only the SHA-256 hash of the emailed token is stored, never the token.
    sa.Column("token_hash", sa.String(64), primary_key=True),
    sa.Column("email", sa.String(254), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True)),
)


mutation_records = sa.Table(
    "mutation_records",
    metadata,
    # The business audit trail: one row per external write across the MCP boundary.
    sa.Column("id", sa.String(36), primary_key=True),
    # MutationRecord has no tenant_id field; the column is stamped by the DB from the
    # connection's tenant GUC (migration 0007), so the ledger is tenant-scoped too.
    sa.Column("tenant_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("workflow_run_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("workflow", sa.String(64), nullable=False, server_default=""),
    # The deal a write concerns (BOP-027); indexed so the transaction hub's audit
    # slice is a direct column match. Authoritative DDL is migration 0011.
    sa.Column("transaction_id", sa.String(36), nullable=False, server_default="", index=True),
    sa.Column("tool", sa.String(64), nullable=False),
    sa.Column("integration", sa.String(32), nullable=False, index=True),
    sa.Column("args", sa.JSON(), nullable=False),
    sa.Column("approval_id", sa.String(36)),
    sa.Column("actor", sa.String(120)),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("external_id", sa.String(120)),
    sa.Column("error", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    # One row per logical write. The key (SHA-256 of run+tool+args) is the primary
    # key, so a duplicate claim is rejected atomically by the DB. result holds the
    # original return value so a completed-key replay can answer without re-executing.
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("workflow_run_id", sa.String(64), nullable=False, server_default="", index=True),
    sa.Column("tool", sa.String(64), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("result", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
)


# Instance-level cron outcome (BOP-035). One row per job name (upserted on each
# run) so /statusz can report "milestones haven't run in 24h". Not tenant-scoped:
# scheduled triggers are per-deploy fleet infrastructure, not agent data.
cron_runs = sa.Table(
    "cron_runs",
    metadata,
    sa.Column("job", sa.String(64), primary_key=True),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("checked", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_pending_escalation", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("email_tail_suppressed", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("error", sa.Text()),
)


def sqlalchemy_url(database_url: str) -> str:
    """Normalize a plain postgres DSN to SQLAlchemy's psycopg dialect."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(sqlalchemy_url(database_url))


class TenantAwareEngine(Protocol):
    """The slice of AsyncEngine the Sql stores use — satisfied by a raw AsyncEngine
    and by TenantScopedEngine, so a store takes either."""

    def begin(self) -> AbstractAsyncContextManager[AsyncConnection]: ...

    def connect(self) -> AbstractAsyncContextManager[AsyncConnection]: ...


class _ScopedTransaction:
    """Opens a transaction and binds the tenant GUC before any statement runs.

    `set_config(..., is_local => true)` scopes the value to this transaction, so it
    never leaks across pooled connections. Reads use a transaction too so the LOCAL
    setting takes effect. ``require_tenant`` fails closed when no tenant is bound.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._cm: AbstractAsyncContextManager[AsyncConnection] | None = None

    async def __aenter__(self) -> AsyncConnection:
        tenant = require_tenant()
        self._cm = self._engine.begin()
        conn = await self._cm.__aenter__()
        await conn.execute(
            sa.text("SELECT set_config('app.brokerops_tenant', :tenant, true)"),
            {"tenant": tenant},
        )
        return conn

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        assert self._cm is not None
        return await self._cm.__aexit__(exc_type, exc, tb)


class TenantScopedEngine:
    """Wraps an AsyncEngine so every unit of work first binds the tenant GUC the
    Postgres row-level-security policy reads (migration 0007). This is the DB belt:
    a query that skips the app-layer scope is still confined at the database."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    def begin(self) -> _ScopedTransaction:
        return _ScopedTransaction(self._engine)

    def connect(self) -> _ScopedTransaction:
        return _ScopedTransaction(self._engine)


def _owned(table: sa.Table) -> sa.ColumnElement[bool]:
    """Row-ownership predicate for the bound tenant (BOP-006): the row's tenant matches
    the bound one, or is unstamped ("" — legacy/raw rows treated as owned, matching the
    scoped wrappers' `_is_foreign`). Used on count/clear/by-id mutations so they confine
    to one tenant at the app-query layer even where the Postgres RLS belt is bypassed
    (superuser). Fails closed: `require_tenant` raises if no tenant is bound."""
    tenant = require_tenant()
    return sa.or_(table.c.tenant_id == tenant, table.c.tenant_id == "")


def _row_to_model(row: Row[tuple[object, ...]]) -> ApprovalRequest:
    return ApprovalRequest.model_validate(dict(row._mapping))


class SqlApprovalRepo:
    def __init__(self, engine: TenantAwareEngine) -> None:
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
        # Tenant-qualified + fail-loud on zero rows (BOP-006 r2): deciding a foreign or
        # missing approval must not silently succeed.
        async with self._engine.begin() as conn:
            result = await conn.execute(
                approval_requests.update()
                .where(approval_requests.c.id == approval_id, _owned(approval_requests))
                .values(status=status.value, decided_by=decided_by, decided_at=decided_at)
            )
        if result.rowcount == 0:
            raise KeyError(approval_id)


class TransactionStoreAdmin(Protocol):
    """Seeding/reset surface used by the demo controls (not by workflows)."""

    async def count_transactions(self) -> int: ...

    async def create_transaction(
        self, transaction: Transaction, txn_milestones: list[Milestone]
    ) -> None: ...

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
    def __init__(self, engine: TenantAwareEngine) -> None:
        self._engine = engine

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                transactions.select().where(transactions.c.id == transaction_id)
            )
            row = result.first()
        return Transaction.model_validate(dict(row._mapping)) if row is not None else None

    async def get_milestone(self, milestone_id: str) -> Milestone | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(milestones.select().where(milestones.c.id == milestone_id))
            row = result.first()
        return Milestone.model_validate(dict(row._mapping)) if row is not None else None

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
        # Tenant-qualify the UPDATE (belt for the superuser path where RLS is inert) and
        # fail loud on a zero-row update: a foreign or missing id must not be a silent
        # no-op (BOP-006 review round 2). Under a hardened role RLS also hides the row,
        # so this raise is the loud signal there.
        async with self._engine.begin() as conn:
            result = await conn.execute(
                milestones.update()
                .where(milestones.c.id == milestone_id, _owned(milestones))
                .values(escalation_level=level)
            )
        if result.rowcount == 0:
            raise KeyError(milestone_id)

    async def count_transactions(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(sa.func.count()).select_from(transactions).where(_owned(transactions))
            )
        return int(result.scalar_one())

    async def create_transaction(
        self, transaction: Transaction, txn_milestones: list[Milestone]
    ) -> None:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(transactions.insert().values(**_txn_to_row(transaction)))
                for milestone in txn_milestones:
                    await conn.execute(milestones.insert().values(**_milestone_to_row(milestone)))
        except IntegrityError as exc:
            # Primary-key conflict: another writer already opened this transaction.
            raise TransactionAlreadyExists(transaction.id) from exc

    async def clear(self) -> None:
        # Scoped deletion: only this tenant's rows, so the demo reset can never widen
        # its blast radius to another tenant even on the superuser path (BOP-006 r2).
        async with self._engine.begin() as conn:
            await conn.execute(milestones.delete().where(_owned(milestones)))
            await conn.execute(transactions.delete().where(_owned(transactions)))


class InMemoryTransactionStore:
    def __init__(self) -> None:
        self._transactions: dict[str, Transaction] = {}
        self._milestones: dict[str, Milestone] = {}

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._transactions.get(transaction_id)

    async def list_active_transactions(self) -> list[Transaction]:
        return [t for t in self._transactions.values() if t.is_active]

    async def get_milestone(self, milestone_id: str) -> Milestone | None:
        return self._milestones.get(milestone_id)

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        found = [m for m in self._milestones.values() if m.transaction_id == transaction_id]
        return sorted(found, key=lambda m: m.due_date)

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        existing = self._milestones[milestone_id]
        self._milestones[milestone_id] = existing.model_copy(update={"escalation_level": level})

    async def count_transactions(self) -> int:
        owned = (require_tenant(), "")
        return sum(1 for t in self._transactions.values() if t.tenant_id in owned)

    async def create_transaction(
        self, transaction: Transaction, txn_milestones: list[Milestone]
    ) -> None:
        if transaction.id in self._transactions:
            raise TransactionAlreadyExists(transaction.id)
        self._transactions[transaction.id] = transaction
        for milestone in txn_milestones:
            self._milestones[milestone.id] = milestone

    async def clear(self) -> None:
        # Delete only this tenant's (or unstamped) rows; another tenant's survive.
        owned = (require_tenant(), "")
        self._transactions = {
            k: v for k, v in self._transactions.items() if v.tenant_id not in owned
        }
        self._milestones = {k: v for k, v in self._milestones.items() if v.tenant_id not in owned}


def _document_to_row(document: Document) -> dict[str, Any]:
    row = document.model_dump(mode="json")
    row["created_at"] = document.created_at
    return row


class SqlDocumentStore:
    def __init__(self, engine: TenantAwareEngine) -> None:
        self._engine = engine

    async def add(self, document: Document) -> None:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(documents.insert().values(**_document_to_row(document)))
        except IntegrityError as exc:
            # Primary-key conflict: another writer already attached this file.
            raise DocumentAlreadyExists(document.id) from exc

    async def get(self, document_id: str) -> Document | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(documents.select().where(documents.c.id == document_id))
            row = result.first()
        return Document.model_validate(dict(row._mapping)) if row is not None else None

    async def list_for_transaction(self, transaction_id: str) -> list[Document]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                documents.select()
                .where(documents.c.transaction_id == transaction_id)
                .order_by(documents.c.created_at)
            )
            rows = result.all()
        return [Document.model_validate(dict(row._mapping)) for row in rows]


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    async def add(self, document: Document) -> None:
        if document.id in self._documents:
            raise DocumentAlreadyExists(document.id)
        self._documents[document.id] = document

    async def get(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    async def list_for_transaction(self, transaction_id: str) -> list[Document]:
        found = [d for d in self._documents.values() if d.transaction_id == transaction_id]
        return sorted(found, key=lambda d: d.created_at)


class SqlFeedbackStore:
    def __init__(self, engine: TenantAwareEngine) -> None:
        self._engine = engine

    async def save_call_record(self, record: CallRecord) -> None:
        values = record.model_dump(mode="json")
        values["created_at"] = record.created_at
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                call_records.select().where(call_records.c.vapi_call_id == record.vapi_call_id)
            )
            if existing.first() is None:
                await conn.execute(call_records.insert().values(**values))
            else:
                await conn.execute(
                    call_records.update()
                    .where(call_records.c.vapi_call_id == record.vapi_call_id)
                    .values(**values)
                )

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                call_records.select().where(call_records.c.vapi_call_id == vapi_call_id)
            )
            row = result.first()
        return CallRecord.model_validate(dict(row._mapping)) if row is not None else None

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str:
        values = feedback.model_dump(mode="json")
        values["created_at"] = feedback.created_at
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                showing_feedback.select().where(showing_feedback.c.id == feedback.id)
            )
            if existing.first() is None:
                await conn.execute(showing_feedback.insert().values(**values))
            else:
                await conn.execute(
                    showing_feedback.update()
                    .where(showing_feedback.c.id == feedback.id)
                    .values(**values)
                )
        return feedback.id

    async def get_feedback(self, feedback_id: str) -> ShowingFeedback | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                showing_feedback.select().where(showing_feedback.c.id == feedback_id)
            )
            row = result.first()
        return ShowingFeedback.model_validate(dict(row._mapping)) if row is not None else None

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                showing_feedback.select().where(showing_feedback.c.listing_key == listing_key)
            )
            rows = result.all()
        return [ShowingFeedback.model_validate(dict(row._mapping)) for row in rows]


class InMemoryFeedbackStore:
    def __init__(self) -> None:
        self._call_records: dict[str, CallRecord] = {}
        self._feedback: dict[str, ShowingFeedback] = {}

    async def save_call_record(self, record: CallRecord) -> None:
        self._call_records[record.vapi_call_id] = record

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None:
        return self._call_records.get(vapi_call_id)

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str:
        self._feedback[feedback.id] = feedback
        return feedback.id

    async def get_feedback(self, feedback_id: str) -> ShowingFeedback | None:
        return self._feedback.get(feedback_id)

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        return [f for f in self._feedback.values() if f.listing_key == listing_key]


class SqlMessageStore:
    def __init__(self, engine: TenantAwareEngine) -> None:
        self._engine = engine

    async def save_message(self, message: Message) -> None:
        values = message.model_dump(mode="json")
        values["created_at"] = message.created_at
        values["sent_at"] = message.sent_at
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                outbound_messages.select().where(outbound_messages.c.id == message.id)
            )
            if existing.first() is None:
                await conn.execute(outbound_messages.insert().values(**values))
            else:
                await conn.execute(
                    outbound_messages.update()
                    .where(outbound_messages.c.id == message.id)
                    .values(**values)
                )

    async def get_message(self, message_id: str) -> Message | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                outbound_messages.select().where(outbound_messages.c.id == message_id)
            )
            row = result.first()
        return Message.model_validate(dict(row._mapping)) if row is not None else None

    async def get_message_by_provider_id(self, provider_message_id: str) -> Message | None:
        if not provider_message_id:
            return None  # every not-yet-sent row holds "" — never match those
        async with self._engine.connect() as conn:
            result = await conn.execute(
                outbound_messages.select().where(
                    outbound_messages.c.provider_message_id == provider_message_id
                )
            )
            row = result.first()
        return Message.model_validate(dict(row._mapping)) if row is not None else None

    async def list_messages(
        self,
        contact_id: str | None = None,
        limit: int = 100,
        transaction_id: str | None = None,
    ) -> list[Message]:
        query = (
            outbound_messages.select().order_by(outbound_messages.c.created_at.desc()).limit(limit)
        )
        if contact_id is not None:
            query = query.where(outbound_messages.c.contact_id == contact_id)
        if transaction_id is not None:
            query = query.where(outbound_messages.c.transaction_id == transaction_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            rows = result.all()
        return [Message.model_validate(dict(row._mapping)) for row in rows]

    async def advance_message_status(
        self, message_id: str, status: MessageStatus
    ) -> Message | None:
        # Forward-only, decided at the database (BOP-037): the UPDATE matches only
        # rows still at a strictly lower rank, so two racing delivery callbacks
        # can't both win — and only the status column is written, never a stale
        # full-row snapshot.
        lower_ranked = [s.value for s in MessageStatus if STATUS_RANK[s] < STATUS_RANK[status]]
        async with self._engine.begin() as conn:
            await conn.execute(
                outbound_messages.update()
                .where(
                    outbound_messages.c.id == message_id,
                    outbound_messages.c.status.in_(lower_ranked),
                )
                .values(status=status.value)
            )
            result = await conn.execute(
                outbound_messages.select().where(outbound_messages.c.id == message_id)
            )
            row = result.first()
        return Message.model_validate(dict(row._mapping)) if row is not None else None


class InMemoryMessageStore:
    def __init__(self) -> None:
        self._messages: dict[str, Message] = {}

    async def save_message(self, message: Message) -> None:
        self._messages[message.id] = message

    async def get_message(self, message_id: str) -> Message | None:
        return self._messages.get(message_id)

    async def get_message_by_provider_id(self, provider_message_id: str) -> Message | None:
        if not provider_message_id:
            return None  # every not-yet-sent row holds "" — never match those
        for message in self._messages.values():
            if message.provider_message_id == provider_message_id:
                return message
        return None

    async def list_messages(
        self,
        contact_id: str | None = None,
        limit: int = 100,
        transaction_id: str | None = None,
    ) -> list[Message]:
        found = [
            m
            for m in self._messages.values()
            if (contact_id is None or m.contact_id == contact_id)
            and (transaction_id is None or m.transaction_id == transaction_id)
        ]
        epoch = datetime.min.replace(tzinfo=UTC)
        found.sort(key=lambda m: m.created_at or epoch, reverse=True)
        return found[:limit]

    async def advance_message_status(
        self, message_id: str, status: MessageStatus
    ) -> Message | None:
        # Forward-only and atomic under the event loop (no await between the rank
        # check and the write), mirroring the SQL store's conditional UPDATE.
        row = self._messages.get(message_id)
        if row is None:
            return None
        if STATUS_RANK[status] > STATUS_RANK[row.status]:
            row = row.model_copy(update={"status": status})
            self._messages[message_id] = row
        return row


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


class SqlMagicTokenStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, token_hash: str, email: str, expires_at: datetime) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                magic_login_tokens.insert().values(
                    token_hash=token_hash, email=email, expires_at=expires_at
                )
            )

    async def consume(self, token_hash: str) -> ConsumedToken | None:
        # Single atomic claim: flip consumed_at only if still unused, returning
        # the row. A second redemption matches no unconsumed row → None.
        async with self._engine.begin() as conn:
            result = await conn.execute(
                magic_login_tokens.update()
                .where(
                    magic_login_tokens.c.token_hash == token_hash,
                    magic_login_tokens.c.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.now(UTC))
                .returning(magic_login_tokens.c.email, magic_login_tokens.c.expires_at)
            )
            row = result.first()
        return ConsumedToken.model_validate(dict(row._mapping)) if row is not None else None


class SqlAuditLog:
    def __init__(self, engine: TenantAwareEngine) -> None:
        self._engine = engine

    async def record(self, record: MutationRecord) -> None:
        values = record.model_dump(mode="json")
        values["created_at"] = record.created_at
        async with self._engine.begin() as conn:
            await conn.execute(mutation_records.insert().values(**values))

    # Defined before `list` so its `-> list[MutationRecord]` annotation resolves to
    # the builtin, not this class's later-bound `list` method (a real mypy
    # valid-type trap when a method named `list` precedes another annotation).
    async def list_for_transaction(
        self, transaction_id: str, limit: int = 200
    ) -> list[MutationRecord]:
        # A direct indexed column match (BOP-027): every write from a
        # transaction-scoped run is stamped with the deal id at record time, so the
        # slice is complete regardless of whether the run raised an approval, and
        # never bounded by a scan window.
        query = (
            mutation_records.select()
            .where(mutation_records.c.transaction_id == transaction_id)
            .order_by(mutation_records.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            rows = result.all()
        return [MutationRecord.model_validate(dict(row._mapping)) for row in rows]

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        query = (
            mutation_records.select().order_by(mutation_records.c.created_at.desc()).limit(limit)
        )
        if workflow_run_id is not None:
            query = query.where(mutation_records.c.workflow_run_id == workflow_run_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            rows = result.all()
        return [MutationRecord.model_validate(dict(row._mapping)) for row in rows]


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._items: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self._items.append(record)

    # Before `list` for the same valid-type reason as SqlAuditLog above.
    async def list_for_transaction(
        self, transaction_id: str, limit: int = 200
    ) -> list[MutationRecord]:
        items = sorted(self._items, key=lambda r: r.created_at, reverse=True)
        matched = [r for r in items if r.transaction_id == transaction_id]
        return matched[:limit]

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        items = sorted(self._items, key=lambda r: r.created_at, reverse=True)
        if workflow_run_id is not None:
            items = [r for r in items if r.workflow_run_id == workflow_run_id]
        return items[:limit]


class SqlIdempotencyStore:
    """Durable write-tool dedupe. The primary-key insert is the atomic claim: the
    first writer inserts a pending row; a concurrent or replayed claim hits the
    uniqueness constraint, reads the existing row, and learns whether the original
    completed (return its result) or is still pending (a mid-write crash).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    idempotency_keys.insert().values(
                        key=key,
                        workflow_run_id=workflow_run_id,
                        tool=tool,
                        status=ClaimStatus.PENDING.value,
                        created_at=datetime.now(UTC),
                    )
                )
            return IdempotencyClaim(status=ClaimStatus.NEW)
        except IntegrityError:
            # Key already claimed — read where the prior attempt got to.
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    idempotency_keys.select().where(idempotency_keys.c.key == key)
                )
                row = result.first()
        mapping = row._mapping  # type: ignore[union-attr]  # row exists: the insert conflicted
        return IdempotencyClaim(status=ClaimStatus(mapping["status"]), result=mapping["result"])

    async def complete(self, key: str, result: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                idempotency_keys.update()
                .where(idempotency_keys.c.key == key)
                .values(
                    status=ClaimStatus.COMPLETED.value,
                    result=result,
                    completed_at=datetime.now(UTC),
                )
            )


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        # key -> (status, result)
        self._rows: dict[str, tuple[ClaimStatus, str | None]] = {}

    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = (ClaimStatus.PENDING, None)
            return IdempotencyClaim(status=ClaimStatus.NEW)
        status, result = existing
        return IdempotencyClaim(status=status, result=result)

    async def complete(self, key: str, result: str) -> None:
        self._rows[key] = (ClaimStatus.COMPLETED, result)


class InMemoryMagicTokenStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[str, datetime]] = {}
        self._consumed: set[str] = set()

    async def create(self, token_hash: str, email: str, expires_at: datetime) -> None:
        self._rows[token_hash] = (email, expires_at)

    async def consume(self, token_hash: str) -> ConsumedToken | None:
        if token_hash not in self._rows or token_hash in self._consumed:
            return None
        self._consumed.add(token_hash)
        email, expires_at = self._rows[token_hash]
        return ConsumedToken(email=email, expires_at=expires_at)


class CronRunRecord:
    """Latest outcome of one scheduled job (BOP-035). Plain dataclass-shaped
    namespace kept next to the store so the status surface doesn't need a core
    domain model for fleet-ops bookkeeping."""

    __slots__ = (
        "job",
        "outcome",
        "finished_at",
        "checked",
        "skipped_pending_escalation",
        "email_tail_suppressed",
        "error",
    )

    def __init__(
        self,
        *,
        job: str,
        outcome: str,
        finished_at: datetime,
        checked: int = 0,
        skipped_pending_escalation: int = 0,
        email_tail_suppressed: int = 0,
        error: str | None = None,
    ) -> None:
        self.job = job
        self.outcome = outcome
        self.finished_at = finished_at
        self.checked = checked
        self.skipped_pending_escalation = skipped_pending_escalation
        self.email_tail_suppressed = email_tail_suppressed
        self.error = error

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "outcome": self.outcome,
            "finished_at": self.finished_at.isoformat(),
            "checked": self.checked,
            "skipped_pending_escalation": self.skipped_pending_escalation,
            "email_tail_suppressed": self.email_tail_suppressed,
            "error": self.error,
        }


class CronRunStore(Protocol):
    async def record(
        self,
        job: str,
        *,
        outcome: str,
        checked: int = 0,
        skipped_pending_escalation: int = 0,
        email_tail_suppressed: int = 0,
        error: str | None = None,
    ) -> CronRunRecord: ...

    async def latest(self, job: str) -> CronRunRecord | None: ...


class SqlCronRunStore:
    """Durable last-run upsert for scheduled jobs. Uses the raw engine (no
    tenant GUC) — cron is per-instance infrastructure, not tenant agent data."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(
        self,
        job: str,
        *,
        outcome: str,
        checked: int = 0,
        skipped_pending_escalation: int = 0,
        email_tail_suppressed: int = 0,
        error: str | None = None,
    ) -> CronRunRecord:
        finished_at = datetime.now(UTC)
        record = CronRunRecord(
            job=job,
            outcome=outcome,
            finished_at=finished_at,
            checked=checked,
            skipped_pending_escalation=skipped_pending_escalation,
            email_tail_suppressed=email_tail_suppressed,
            error=error,
        )
        values = {
            "job": job,
            "outcome": outcome,
            "finished_at": finished_at,
            "checked": checked,
            "skipped_pending_escalation": skipped_pending_escalation,
            "email_tail_suppressed": email_tail_suppressed,
            "error": error,
        }
        # Dialect-aware atomic upsert (one row per job). Prefer the engine's
        # native ON CONFLICT path so concurrent scheduler retries never race the
        # primary key; fall back to insert-or-update on IntegrityError.
        conflict_set = {
            "outcome": outcome,
            "finished_at": finished_at,
            "checked": checked,
            "skipped_pending_escalation": skipped_pending_escalation,
            "email_tail_suppressed": email_tail_suppressed,
            "error": error,
        }
        dialect = self._engine.dialect.name
        async with self._engine.begin() as conn:
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                pg_stmt = pg_insert(cron_runs).values(**values)
                await conn.execute(
                    pg_stmt.on_conflict_do_update(
                        index_elements=[cron_runs.c.job], set_=conflict_set
                    )
                )
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                sqlite_stmt = sqlite_insert(cron_runs).values(**values)
                await conn.execute(
                    sqlite_stmt.on_conflict_do_update(
                        index_elements=[cron_runs.c.job], set_=conflict_set
                    )
                )
            else:
                try:
                    await conn.execute(cron_runs.insert().values(**values))
                except IntegrityError:
                    await conn.execute(
                        cron_runs.update().where(cron_runs.c.job == job).values(**conflict_set)
                    )
        return record

    async def latest(self, job: str) -> CronRunRecord | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(cron_runs.select().where(cron_runs.c.job == job))
            row = result.first()
        if row is None:
            return None
        m = row._mapping
        return CronRunRecord(
            job=str(m["job"]),
            outcome=str(m["outcome"]),
            finished_at=m["finished_at"],
            checked=int(m["checked"] or 0),
            skipped_pending_escalation=int(m["skipped_pending_escalation"] or 0),
            email_tail_suppressed=int(m["email_tail_suppressed"] or 0),
            error=m["error"],
        )


class InMemoryCronRunStore:
    def __init__(self) -> None:
        self._rows: dict[str, CronRunRecord] = {}

    async def record(
        self,
        job: str,
        *,
        outcome: str,
        checked: int = 0,
        skipped_pending_escalation: int = 0,
        email_tail_suppressed: int = 0,
        error: str | None = None,
    ) -> CronRunRecord:
        record = CronRunRecord(
            job=job,
            outcome=outcome,
            finished_at=datetime.now(UTC),
            checked=checked,
            skipped_pending_escalation=skipped_pending_escalation,
            email_tail_suppressed=email_tail_suppressed,
            error=error,
        )
        self._rows[job] = record
        return record

    async def latest(self, job: str) -> CronRunRecord | None:
        return self._rows.get(job)
