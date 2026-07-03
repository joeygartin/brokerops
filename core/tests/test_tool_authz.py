"""Unit coverage for tool-input authorization (BOP-011).

Proves the tool-input half of the tenant seam: a tenant-bearing parameter carrying a
foreign tenant id is rejected *before* the wrapped tool body runs — so no store/port
method is ever reached — while an in-scope call passes through unchanged. Storage- and
engine-agnostic: the guard is a decorator over the Pydantic tool-input boundary.
"""

import logging
from datetime import date

import pytest

from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.tenancy import CrossTenantError, TenantContextMissing, tenant_scope
from brokerops_core.services.tool_authz import (
    AUTHORIZED_MARKER,
    accepts_tenant_bearing_param,
    annotation_is_tenant_bearing,
    authorize_tenant_params,
    authorize_tool_ports,
)


def _txn(tenant_id: str) -> Transaction:
    return Transaction(
        tenant_id=tenant_id,
        id="TXN-1",
        listing_key="L1",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 1, 1),
    )


def _milestone(tenant_id: str) -> Milestone:
    return Milestone(
        tenant_id=tenant_id,
        id="M1",
        transaction_id="TXN-1",
        type=MilestoneType.INSPECTION,
        title="Inspection",
        due_date=date(2026, 1, 8),
    )


class _SpyStore:
    """A minimal tenant-bearing store that records whether a write was reached."""

    def __init__(self) -> None:
        self.reached: list[str] = []

    async def create_transaction(
        self, transaction: Transaction, milestones: list[Milestone], /
    ) -> None:
        self.reached.append("create_transaction")

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        self.reached.append("get_transaction")
        return None


async def test_foreign_tenant_param_rejected_before_store_is_reached() -> None:
    store = authorize_tool_ports(_SpyStore())
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError) as exc:
            await store.create_transaction(_txn("other-brokerage"), [])
    assert exc.value.attempted == "other-brokerage"
    assert exc.value.bound == "demo"
    # The acceptance guarantee: the wrapped store body was never entered.
    assert store.reached == []


async def test_foreign_tenant_in_a_batched_param_is_rejected() -> None:
    # A foreign tenant hiding in the milestone list (not the transaction) is still caught.
    store = authorize_tool_ports(_SpyStore())
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await store.create_transaction(_txn("demo"), [_milestone("other-brokerage")])
    assert store.reached == []


async def test_in_scope_call_passes_through_unchanged() -> None:
    store = authorize_tool_ports(_SpyStore())
    with tenant_scope("demo"):
        # An empty tenant ("use the ambient tenant") and the matching tenant both pass.
        await store.create_transaction(_txn(""), [_milestone("")])
        await store.create_transaction(_txn("demo"), [_milestone("demo")])
    assert store.reached == ["create_transaction", "create_transaction"]


async def test_read_without_a_tenant_bearing_arg_passes_through() -> None:
    store = authorize_tool_ports(_SpyStore())
    with tenant_scope("demo"):
        result = await store.get_transaction("TXN-1")
    assert result is None
    assert store.reached == ["get_transaction"]


async def test_no_bound_tenant_fails_closed_on_a_tenant_bearing_call() -> None:
    store = authorize_tool_ports(_SpyStore())
    with pytest.raises(TenantContextMissing):
        await store.create_transaction(_txn("demo"), [])
    assert store.reached == []


async def test_rejection_logs_tool_and_param_but_not_the_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = authorize_tool_ports(_SpyStore())
    with tenant_scope("demo"), caplog.at_level(logging.WARNING):
        with pytest.raises(CrossTenantError):
            await store.create_transaction(_txn("other-brokerage"), [])
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("create_transaction" in m and "param=" in m for m in messages)
    # The foreign tenant id and any payload field must never be logged.
    assert all("other-brokerage" not in m and "listing_key" not in m for m in messages)


def test_authorize_tool_ports_is_marked_and_idempotent() -> None:
    store = _SpyStore()
    once = authorize_tool_ports(store)
    assert getattr(once, AUTHORIZED_MARKER, False) is True
    # Re-wrapping is a no-op (same instance, methods not double-wrapped).
    twice = authorize_tool_ports(once)
    assert twice is once


def test_authorize_tenant_params_marks_the_wrapper() -> None:
    async def tool(transaction: Transaction) -> None:
        return None

    wrapped = authorize_tenant_params(tool)
    assert getattr(wrapped, AUTHORIZED_MARKER, False) is True


def test_annotation_detection_covers_models_and_containers() -> None:
    assert annotation_is_tenant_bearing(Transaction) is True
    assert annotation_is_tenant_bearing(list[Milestone]) is True
    assert annotation_is_tenant_bearing(Transaction | None) is True
    assert annotation_is_tenant_bearing(str) is False
    assert annotation_is_tenant_bearing(list[str]) is False


def test_accepts_tenant_bearing_param_reads_signatures() -> None:
    assert accepts_tenant_bearing_param(_SpyStore.create_transaction) is True
    assert accepts_tenant_bearing_param(_SpyStore.get_transaction) is False
