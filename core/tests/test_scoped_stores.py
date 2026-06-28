"""Tenant isolation coverage for the scoped store wrappers (BOP-006).

Uses inline in-test fakes for the inner stores so the test stays in core (core must
not import the api package). Proves: a write stamps the bound tenant; a write
carrying a foreign tenant id is denied + audited; a by-id read never returns another
tenant's row; access with no tenant bound fails closed.
"""

from datetime import UTC, date, datetime

import pytest

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.scoped_stores import (
    ScopedApprovalRepo,
    ScopedFeedbackStore,
    ScopedTransactionStore,
)
from brokerops_core.services.tenancy import (
    CrossTenantError,
    TenantContextMissing,
    tenant_scope,
)


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


class FakeTransactionStore:
    def __init__(self) -> None:
        self.transactions: dict[str, Transaction] = {}
        self.milestones: dict[str, Milestone] = {}

    async def create_transaction(
        self, transaction: Transaction, milestones: list[Milestone], /
    ) -> None:
        self.transactions[transaction.id] = transaction
        for milestone in milestones:
            self.milestones[milestone.id] = milestone

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self.transactions.get(transaction_id)

    async def get_milestone(self, milestone_id: str) -> Milestone | None:
        return self.milestones.get(milestone_id)

    async def list_active_transactions(self) -> list[Transaction]:
        return [t for t in self.transactions.values() if t.is_active]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        return [m for m in self.milestones.values() if m.transaction_id == transaction_id]

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        existing = self.milestones[milestone_id]
        self.milestones[milestone_id] = existing.model_copy(update={"escalation_level": level})

    async def count_transactions(self) -> int:
        return len(self.transactions)

    async def clear(self) -> None:
        self.transactions.clear()
        self.milestones.clear()


class FakeFeedbackStore:
    def __init__(self) -> None:
        self.call_records: dict[str, CallRecord] = {}
        self.feedback: dict[str, ShowingFeedback] = {}

    async def save_call_record(self, record: CallRecord) -> None:
        self.call_records[record.vapi_call_id] = record

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None:
        return self.call_records.get(vapi_call_id)

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str:
        self.feedback[feedback.id] = feedback
        return feedback.id

    async def get_feedback(self, feedback_id: str) -> ShowingFeedback | None:
        return self.feedback.get(feedback_id)

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        return [f for f in self.feedback.values() if f.listing_key == listing_key]


class FakeApprovalRepo:
    def __init__(self) -> None:
        self.items: dict[str, ApprovalRequest] = {}

    async def create(self, approval: ApprovalRequest) -> None:
        self.items[approval.id] = approval

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.items.get(approval_id)

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        return [a for a in self.items.values() if status is None or a.status is status]

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None:
        existing = self.items[approval_id]
        self.items[approval_id] = existing.model_copy(
            update={"status": status, "decided_by": decided_by, "decided_at": decided_at}
        )


def _txn(txn_id: str, *, tenant_id: str = "") -> Transaction:
    return Transaction(
        tenant_id=tenant_id,
        id=txn_id,
        listing_key="L1",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 1, 1),
    )


def _milestone(ms_id: str, txn_id: str, *, tenant_id: str = "") -> Milestone:
    return Milestone(
        tenant_id=tenant_id,
        id=ms_id,
        transaction_id=txn_id,
        type=MilestoneType.INSPECTION,
        title="Inspection",
        due_date=date(2026, 1, 10),
    )


def _approval(approval_id: str, *, tenant_id: str = "") -> ApprovalRequest:
    return ApprovalRequest(
        tenant_id=tenant_id,
        id=approval_id,
        workflow="listing_to_contract",
        graph_thread_id="t1",
        kind="approve_marketing",
        payload={},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_stamps_ambient_tenant() -> None:
    inner = FakeTransactionStore()
    store = ScopedTransactionStore(inner, CollectingAuditLog())
    with tenant_scope("demo"):
        await store.create_transaction(_txn("tx1"), [_milestone("m1", "tx1")])
    assert inner.transactions["tx1"].tenant_id == "demo"
    assert inner.milestones["m1"].tenant_id == "demo"


@pytest.mark.asyncio
async def test_create_with_foreign_tenant_is_denied_and_audited() -> None:
    inner = FakeTransactionStore()
    audit = CollectingAuditLog()
    store = ScopedTransactionStore(inner, audit)
    # A prompt-injected node populated tenant_id with another brokerage's id.
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await store.create_transaction(_txn("tx1", tenant_id="other-brokerage"), [])
    assert inner.transactions == {}  # nothing written
    assert len(audit.records) == 1
    assert audit.records[0].integration == "security"
    assert audit.records[0].args == {
        "attempted_tenant": "other-brokerage",
        "bound_tenant": "demo",
    }


@pytest.mark.asyncio
async def test_get_hides_another_tenants_row_and_audits() -> None:
    inner = FakeTransactionStore()
    audit = CollectingAuditLog()
    # A row belonging to brokerage-a already exists in the (shared-future) store.
    inner.transactions["tx-a"] = _txn("tx-a", tenant_id="brokerage-a")
    store = ScopedTransactionStore(inner, audit)
    with tenant_scope("brokerage-b"):
        assert await store.get_transaction("tx-a") is None
    assert len(audit.records) == 1
    assert audit.records[0].tool == "get_transaction"


@pytest.mark.asyncio
async def test_list_filters_to_bound_tenant() -> None:
    inner = FakeTransactionStore()
    inner.transactions["a"] = _txn("a", tenant_id="brokerage-a")
    inner.transactions["b"] = _txn("b", tenant_id="brokerage-b")
    store = ScopedTransactionStore(inner, CollectingAuditLog())
    with tenant_scope("brokerage-b"):
        active = await store.list_active_transactions()
    assert {t.id for t in active} == {"b"}


@pytest.mark.asyncio
async def test_read_without_tenant_scope_fails_closed() -> None:
    store = ScopedTransactionStore(FakeTransactionStore(), CollectingAuditLog())
    with pytest.raises(TenantContextMissing):
        await store.get_transaction("anything")


@pytest.mark.asyncio
async def test_approval_mark_decided_rejects_foreign_tenant() -> None:
    inner = FakeApprovalRepo()
    audit = CollectingAuditLog()
    inner.items["ap-a"] = _approval("ap-a", tenant_id="brokerage-a")
    repo = ScopedApprovalRepo(inner, audit)
    with tenant_scope("brokerage-b"):
        with pytest.raises(CrossTenantError):
            await repo.mark_decided(
                "ap-a", ApprovalStatus.APPROVED, "attacker@b", datetime.now(UTC)
            )
    # the foreign approval was never decided
    assert inner.items["ap-a"].status is ApprovalStatus.PENDING
    assert any(r.tool == "mark_decided" for r in audit.records)


@pytest.mark.asyncio
async def test_set_escalation_level_rejects_foreign_milestone() -> None:
    # A prompt-injected node supplies another brokerage's milestone id directly.
    inner = FakeTransactionStore()
    audit = CollectingAuditLog()
    inner.milestones["m-a"] = _milestone("m-a", "tx-a", tenant_id="brokerage-a")
    store = ScopedTransactionStore(inner, audit)
    with tenant_scope("brokerage-b"):
        with pytest.raises(CrossTenantError):
            await store.set_escalation_level("m-a", 3)
    assert inner.milestones["m-a"].escalation_level == 0  # untouched
    assert any(r.tool == "set_escalation_level" for r in audit.records)


@pytest.mark.asyncio
async def test_save_call_record_rejects_overwrite_of_foreign_row() -> None:
    # Reuse-a-foreign-id: a node writes a call record whose id already belongs to
    # another tenant. The model claims the bound tenant, but the existing row is foreign.
    inner = FakeFeedbackStore()
    audit = CollectingAuditLog()
    inner.call_records["call-a"] = CallRecord(tenant_id="brokerage-a", vapi_call_id="call-a")
    store = ScopedFeedbackStore(inner, audit)
    with tenant_scope("brokerage-b"):
        with pytest.raises(CrossTenantError):
            await store.save_call_record(CallRecord(vapi_call_id="call-a", transcript="hijack"))
    assert inner.call_records["call-a"].transcript == ""  # not overwritten
    assert any(r.tool == "save_call_record" for r in audit.records)


@pytest.mark.asyncio
async def test_upsert_feedback_rejects_overwrite_of_foreign_row() -> None:
    inner = FakeFeedbackStore()
    audit = CollectingAuditLog()
    inner.feedback["fb-a"] = ShowingFeedback(
        tenant_id="brokerage-a", id="fb-a", listing_key="L1", contact_id="c1"
    )
    store = ScopedFeedbackStore(inner, audit)
    with tenant_scope("brokerage-b"):
        with pytest.raises(CrossTenantError):
            await store.upsert_feedback(
                ShowingFeedback(id="fb-a", listing_key="L9", contact_id="c9")
            )
    assert inner.feedback["fb-a"].listing_key == "L1"  # not overwritten
    assert any(r.tool == "upsert_feedback" for r in audit.records)


@pytest.mark.asyncio
async def test_admin_surface_is_tenant_scoped() -> None:
    # The demo /seed route reaches count_transactions/clear through the scoped store.
    inner = FakeTransactionStore()
    store = ScopedTransactionStore(inner, CollectingAuditLog())
    with tenant_scope("demo"):
        await store.create_transaction(_txn("tx1"), [])
        assert await store.count_transactions() == 1
        await store.clear()
        assert await store.count_transactions() == 0
    # fails closed without a bound tenant
    with pytest.raises(TenantContextMissing):
        await store.count_transactions()
