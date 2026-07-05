"""Tenant-scoped data-access wrappers (BOP-006, req 2).

Each wrapper decorates a persistence port and is the single chokepoint where the
tenant is injected from the bound request context — never from a method argument:

- **Writes that carry a model `tenant_id`** (create/save/upsert) are checked with
  ``enforce_tenant``: a record claiming a *foreign* tenant (an injected node populated
  the field) is denied and recorded as a security event **on every backend**, because
  the check happens before any storage call.
- **By-id mutations of an existing row** (``set_escalation_level`` and the update branch
  of save/upsert) read the target row first and deny+audit if it belongs to another
  tenant — closing the "reuse a foreign id" vector.
- **By-id reads** refuse to return another tenant's row, recording the attempt.
- Any access with no tenant bound fails closed (``require_tenant`` raises).

**Audit scope (be precise):** the deny+audit of *by-id* reads/mutations relies on the
wrapper seeing the target row's `tenant_id`. That holds on the always-on app-layer paths
(in-memory, and the demo/compose Postgres where the superuser bypasses RLS, so the row is
visible). Under a hardened **non-superuser** Postgres role the row-level-security policy
(migration 0007) hides the foreign row from the read *and* blocks the mutation at the
database — so the access is still denied, but the DB does it and no app-layer security
event is emitted (auditing a row RLS has hidden would itself leak its existence). Net:
**the data is confined on every path; the security-event audit is best-effort on the
by-id paths and reliable on the model-`tenant_id` write path.** RLS is the belt; these
wrappers are the always-on suspenders.
"""

from datetime import datetime
from typing import Protocol

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.models.document import Document
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.models.message import Message, MessageStatus
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.transaction import Transaction
from brokerops_core.ports.approvals import ApprovalRepo
from brokerops_core.ports.audit import AuditLog
from brokerops_core.ports.documents import DocumentStore
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.messaging import MessageStore
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.tenancy import (
    CrossTenantError,
    enforce_tenant,
    record_cross_tenant_attempt,
    require_tenant,
)


class _AdminTransactionStore(TransactionStore, Protocol):
    """TransactionStore plus the demo seed/reset surface, so ScopedTransactionStore can
    delegate ``count_transactions``/``clear`` (used by the /demo/seed route) while staying
    a drop-in TransactionStore for the workflow engine. Both Sql and InMemory stores
    satisfy it."""

    async def count_transactions(self) -> int: ...

    async def clear(self) -> None: ...


def _is_foreign(row_tenant: str, ambient: str) -> bool:
    """A non-empty tenant that disagrees with the bound one is another tenant's row.

    An empty tenant (legacy/unstamped) is treated as owned, so pre-scoping rows
    backfilled to the deploy tenant remain reachable.
    """
    return bool(row_tenant) and row_tenant != ambient


class ScopedTransactionStore:
    """TransactionStore decorator that confines reads/writes to the bound tenant."""

    def __init__(self, inner: _AdminTransactionStore, audit: AuditLog) -> None:
        self._inner = inner
        self._audit = audit

    async def create_transaction(
        self, transaction: Transaction, milestones: list[Milestone], /
    ) -> None:
        try:
            tenant = enforce_tenant(transaction.tenant_id)
            for milestone in milestones:
                enforce_tenant(milestone.tenant_id)
        except CrossTenantError as err:
            await record_cross_tenant_attempt(self._audit, err, tool="create_transaction")
            raise
        stamped_txn = transaction.model_copy(update={"tenant_id": tenant})
        stamped_milestones = [m.model_copy(update={"tenant_id": tenant}) for m in milestones]
        await self._inner.create_transaction(stamped_txn, stamped_milestones)

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        ambient = require_tenant()
        transaction = await self._inner.get_transaction(transaction_id)
        if transaction is not None and _is_foreign(transaction.tenant_id, ambient):
            await self._audit_read("get_transaction", transaction.tenant_id, ambient)
            return None
        return transaction

    async def get_milestone(self, milestone_id: str) -> Milestone | None:
        ambient = require_tenant()
        milestone = await self._inner.get_milestone(milestone_id)
        if milestone is not None and _is_foreign(milestone.tenant_id, ambient):
            await self._audit_read("get_milestone", milestone.tenant_id, ambient)
            return None
        return milestone

    async def list_active_transactions(self) -> list[Transaction]:
        ambient = require_tenant()
        rows = await self._inner.list_active_transactions()
        return [t for t in rows if not _is_foreign(t.tenant_id, ambient)]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        ambient = require_tenant()
        rows = await self._inner.list_milestones(transaction_id)
        return [m for m in rows if not _is_foreign(m.tenant_id, ambient)]

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        # A by-id mutation: confirm the target milestone belongs to the bound tenant
        # before updating, so a node cannot escalate another brokerage's milestone by
        # supplying its id. Foreign target → deny + audit (see module audit-scope note).
        ambient = require_tenant()
        milestone = await self._inner.get_milestone(milestone_id)
        if milestone is not None and _is_foreign(milestone.tenant_id, ambient):
            await self._audit_read("set_escalation_level", milestone.tenant_id, ambient)
            raise CrossTenantError(milestone.tenant_id, ambient)
        await self._inner.set_escalation_level(milestone_id, level)

    async def count_transactions(self) -> int:
        # Admin surface used by the demo seed route. Tenant-global within this deploy
        # (single tenant per database); require a bound tenant so it fails closed.
        require_tenant()
        return await self._inner.count_transactions()

    async def clear(self) -> None:
        require_tenant()
        await self._inner.clear()

    async def _audit_read(self, tool: str, attempted: str, bound: str) -> None:
        await record_cross_tenant_attempt(
            self._audit, CrossTenantError(attempted, bound), tool=tool
        )


class ScopedFeedbackStore:
    """FeedbackStore decorator that confines reads/writes to the bound tenant."""

    def __init__(self, inner: FeedbackStore, audit: AuditLog) -> None:
        self._inner = inner
        self._audit = audit

    async def save_call_record(self, record: CallRecord) -> None:
        tenant = await self._stamp(record.tenant_id, "save_call_record")
        # Reject a write that targets an existing call record owned by another tenant
        # (reuse-a-foreign-id vector), not just a foreign claimed tenant on the model.
        existing = await self._inner.get_call_record(record.vapi_call_id)
        if existing is not None and _is_foreign(existing.tenant_id, tenant):
            await self._audit_foreign("save_call_record", existing.tenant_id, tenant)
            raise CrossTenantError(existing.tenant_id, tenant)
        await self._inner.save_call_record(record.model_copy(update={"tenant_id": tenant}))

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None:
        ambient = require_tenant()
        record = await self._inner.get_call_record(vapi_call_id)
        if record is not None and _is_foreign(record.tenant_id, ambient):
            await self._audit_foreign("get_call_record", record.tenant_id, ambient)
            return None
        return record

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str:
        tenant = await self._stamp(feedback.tenant_id, "upsert_feedback")
        existing = await self._inner.get_feedback(feedback.id)
        if existing is not None and _is_foreign(existing.tenant_id, tenant):
            await self._audit_foreign("upsert_feedback", existing.tenant_id, tenant)
            raise CrossTenantError(existing.tenant_id, tenant)
        return await self._inner.upsert_feedback(feedback.model_copy(update={"tenant_id": tenant}))

    async def get_feedback(self, feedback_id: str) -> ShowingFeedback | None:
        ambient = require_tenant()
        feedback = await self._inner.get_feedback(feedback_id)
        if feedback is not None and _is_foreign(feedback.tenant_id, ambient):
            await self._audit_foreign("get_feedback", feedback.tenant_id, ambient)
            return None
        return feedback

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        ambient = require_tenant()
        rows = await self._inner.list_feedback(listing_key)
        return [f for f in rows if not _is_foreign(f.tenant_id, ambient)]

    async def _stamp(self, claimed: str, tool: str) -> str:
        try:
            return enforce_tenant(claimed)
        except CrossTenantError as err:
            await record_cross_tenant_attempt(self._audit, err, tool=tool)
            raise

    async def _audit_foreign(self, tool: str, attempted: str, bound: str) -> None:
        await record_cross_tenant_attempt(
            self._audit, CrossTenantError(attempted, bound), tool=tool
        )


class ScopedMessageStore:
    """MessageStore decorator that confines the comms history to the bound tenant."""

    def __init__(self, inner: MessageStore, audit: AuditLog) -> None:
        self._inner = inner
        self._audit = audit

    async def save_message(self, message: Message) -> None:
        try:
            tenant = enforce_tenant(message.tenant_id)
        except CrossTenantError as err:
            await record_cross_tenant_attempt(self._audit, err, tool="save_message")
            raise
        # Reject a write that targets an existing message owned by another tenant
        # (reuse-a-foreign-id vector), not just a foreign claimed tenant on the model.
        existing = await self._inner.get_message(message.id)
        if existing is not None and _is_foreign(existing.tenant_id, tenant):
            await self._audit_foreign("save_message", existing.tenant_id, tenant)
            raise CrossTenantError(existing.tenant_id, tenant)
        await self._inner.save_message(message.model_copy(update={"tenant_id": tenant}))

    async def get_message(self, message_id: str) -> Message | None:
        ambient = require_tenant()
        message = await self._inner.get_message(message_id)
        if message is not None and _is_foreign(message.tenant_id, ambient):
            await self._audit_foreign("get_message", message.tenant_id, ambient)
            return None
        return message

    async def get_message_by_provider_id(self, provider_message_id: str) -> Message | None:
        # The delivery-webhook lookup (BOP-018): same confinement as get_message —
        # a callback carrying another tenant's provider id resolves to nothing.
        ambient = require_tenant()
        message = await self._inner.get_message_by_provider_id(provider_message_id)
        if message is not None and _is_foreign(message.tenant_id, ambient):
            await self._audit_foreign("get_message_by_provider_id", message.tenant_id, ambient)
            return None
        return message

    async def list_messages(self, contact_id: str | None = None, limit: int = 100) -> list[Message]:
        ambient = require_tenant()
        rows = await self._inner.list_messages(contact_id, limit)
        return [m for m in rows if not _is_foreign(m.tenant_id, ambient)]

    async def advance_message_status(
        self, message_id: str, status: MessageStatus
    ) -> Message | None:
        # By-id mutation of an existing row: same confinement as save_message's
        # update branch — a foreign row is denied + audited, never advanced. The
        # forward-only conditional itself stays atomic in the inner store.
        ambient = require_tenant()
        existing = await self._inner.get_message(message_id)
        if existing is None:
            return None
        if _is_foreign(existing.tenant_id, ambient):
            await self._audit_foreign("advance_message_status", existing.tenant_id, ambient)
            return None
        return await self._inner.advance_message_status(message_id, status)

    async def _audit_foreign(self, tool: str, attempted: str, bound: str) -> None:
        await record_cross_tenant_attempt(
            self._audit, CrossTenantError(attempted, bound), tool=tool
        )


class ScopedDocumentStore:
    """DocumentStore decorator that confines document metadata to the bound tenant
    (BOP-021, same contract as the other wrappers)."""

    def __init__(self, inner: DocumentStore, audit: AuditLog) -> None:
        self._inner = inner
        self._audit = audit

    async def add(self, document: Document) -> None:
        try:
            tenant = enforce_tenant(document.tenant_id)
        except CrossTenantError as err:
            await record_cross_tenant_attempt(self._audit, err, tool="add_document")
            raise
        # Reject a write that targets an existing document owned by another tenant
        # (reuse-a-foreign-id vector), not just a foreign claimed tenant on the model.
        existing = await self._inner.get(document.id)
        if existing is not None and _is_foreign(existing.tenant_id, tenant):
            await self._audit_foreign("add_document", existing.tenant_id, tenant)
            raise CrossTenantError(existing.tenant_id, tenant)
        await self._inner.add(document.model_copy(update={"tenant_id": tenant}))

    async def get(self, document_id: str) -> Document | None:
        ambient = require_tenant()
        document = await self._inner.get(document_id)
        if document is not None and _is_foreign(document.tenant_id, ambient):
            await self._audit_foreign("get_document", document.tenant_id, ambient)
            return None
        return document

    async def list_for_transaction(self, transaction_id: str) -> list[Document]:
        ambient = require_tenant()
        rows = await self._inner.list_for_transaction(transaction_id)
        return [d for d in rows if not _is_foreign(d.tenant_id, ambient)]

    async def _audit_foreign(self, tool: str, attempted: str, bound: str) -> None:
        await record_cross_tenant_attempt(
            self._audit, CrossTenantError(attempted, bound), tool=tool
        )


class ScopedApprovalRepo:
    """ApprovalRepo decorator that confines the HITL spine to the bound tenant."""

    def __init__(self, inner: ApprovalRepo, audit: AuditLog) -> None:
        self._inner = inner
        self._audit = audit

    async def create(self, approval: ApprovalRequest) -> None:
        try:
            tenant = enforce_tenant(approval.tenant_id)
        except CrossTenantError as err:
            await record_cross_tenant_attempt(self._audit, err, tool="create_approval")
            raise
        await self._inner.create(approval.model_copy(update={"tenant_id": tenant}))

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        ambient = require_tenant()
        approval = await self._inner.get(approval_id)
        if approval is not None and _is_foreign(approval.tenant_id, ambient):
            await record_cross_tenant_attempt(
                self._audit, CrossTenantError(approval.tenant_id, ambient), tool="get_approval"
            )
            return None
        return approval

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        ambient = require_tenant()
        rows = await self._inner.list(status)
        return [a for a in rows if not _is_foreign(a.tenant_id, ambient)]

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None:
        ambient = require_tenant()
        existing = await self._inner.get(approval_id)
        if existing is not None and _is_foreign(existing.tenant_id, ambient):
            await record_cross_tenant_attempt(
                self._audit, CrossTenantError(existing.tenant_id, ambient), tool="mark_decided"
            )
            raise CrossTenantError(existing.tenant_id, ambient)
        await self._inner.mark_decided(approval_id, status, decided_by, decided_at)
