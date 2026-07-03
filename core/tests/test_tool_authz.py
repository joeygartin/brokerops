"""Unit coverage for tool-input authorization (BOP-011).

Proves the tool-input half of the tenant seam: a tenant-bearing parameter carrying a
foreign tenant id is rejected *before* the wrapped tool body runs — so no store/port
method is ever reached — while an in-scope call passes through unchanged. Storage- and
engine-agnostic: the guard is a decorator over the Pydantic tool-input boundary.
"""

import logging
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from brokerops_core.models.call import CallRecord
from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.audit import AuditContext, RecordingVoice, audit_scope
from brokerops_core.services.idempotency import IdempotentVoice
from brokerops_core.services.tenancy import CrossTenantError, TenantContextMissing, tenant_scope
from brokerops_core.services.tool_authz import (
    AUTHORIZED_MARKER,
    accepts_tenant_bearing_param,
    annotation_is_tenant_bearing,
    authorize_tenant_params,
    authorize_tool_ports,
)


class _Nested(BaseModel):
    """A tool-input model that only *nests* a tenant-bearing model (no top-level tenant)."""

    label: str = ""
    inner: Transaction


class _FakeRecord(BaseModel):
    """A non-tenant model with a dict field (shaped like MutationRecord) — must NOT be
    treated as tenant-bearing, so wrapping the audit ledger is never forced (no cascade)."""

    name: str
    args: dict[str, Any]


class _Cyclic(BaseModel):
    """A self-referential model, to prove cycle protection terminates."""

    tenant_id: str = ""
    child: "_Cyclic | None" = None


_Cyclic.model_rebuild()


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


async def test_default_empty_tenant_model_still_requires_a_bound_scope() -> None:
    # Finding 1 regression: the common "stamped-later" shape (tenant_id="") must still run
    # require_tenant() before the store body, so it can never reach a store fail-open.
    store = authorize_tool_ports(_SpyStore())
    with pytest.raises(TenantContextMissing):
        await store.create_transaction(_txn(""), [])
    assert store.reached == []


async def test_bare_tenant_id_scalar_param_is_rejected_before_the_body() -> None:
    # Defensive belt: a scalar arg named tenant_id (a shape ADR-0012 forbids) is still
    # authorized at runtime rather than silently ignored.
    reached: list[str] = []

    async def tool(tenant_id: str) -> None:
        reached.append(tenant_id)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await guarded("other-brokerage")
    assert reached == []


async def test_dict_payload_carrying_a_foreign_tenant_is_rejected() -> None:
    # Defensive belt: a mapping smuggling a tenant_id key is caught at runtime.
    reached: list[dict[str, str]] = []

    async def tool(payload: dict[str, str]) -> None:
        reached.append(payload)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await guarded({"tenant_id": "other-brokerage"})
    assert reached == []


async def test_dict_without_a_tenant_id_key_passes_through() -> None:
    reached: list[dict[str, str]] = []

    async def tool(payload: dict[str, str]) -> None:
        reached.append(payload)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        await guarded({"listing_key": "L1"})
    assert reached == [{"listing_key": "L1"}]


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


def test_static_detection_matches_the_runtime_surface() -> None:
    # Static detection covers the SAME surface _tenant_values enforces at runtime: models
    # (incl. nested), a bare tenant_id-named arg, and a top-level mapping/dict. A non-tenant
    # scalar and a non-tenant model with only a dict field are deliberately NOT flagged, so
    # the audit ledger (MutationRecord-shaped) is never dragged in (no cascade).
    async def model_tool(transaction: Transaction) -> None: ...

    async def nested_tool(wrapper: _Nested) -> None: ...

    async def named_tool(tenant_id: str) -> None: ...

    async def dict_tool(payload: dict[str, str]) -> None: ...

    async def scalar_tool(contact_id: str) -> None: ...

    async def fake_record_tool(record: _FakeRecord) -> None: ...

    assert accepts_tenant_bearing_param(model_tool) is True
    assert accepts_tenant_bearing_param(nested_tool) is True
    assert accepts_tenant_bearing_param(named_tool) is True
    assert accepts_tenant_bearing_param(dict_tool) is True
    assert accepts_tenant_bearing_param(scalar_tool) is False
    assert accepts_tenant_bearing_param(fake_record_tool) is False


async def test_nested_tenant_model_foreign_is_rejected_before_the_body() -> None:
    # Finding 2: a foreign tenant hiding in a NESTED model (the wrapper has no top-level
    # tenant_id) is still caught before the tool body.
    reached: list[_Nested] = []

    async def tool(wrapper: _Nested) -> None:
        reached.append(wrapper)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await guarded(_Nested(inner=_txn("other-brokerage")))
    assert reached == []


async def test_nested_tenant_model_matching_passes_through() -> None:
    reached: list[_Nested] = []

    async def tool(wrapper: _Nested) -> None:
        reached.append(wrapper)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        await guarded(_Nested(inner=_txn("demo")))
    assert len(reached) == 1


async def test_self_referential_model_terminates_and_authorizes() -> None:
    # Cycle protection: a model that references itself must not recurse forever.
    node = _Cyclic(tenant_id="demo")
    node.child = node
    reached: list[_Cyclic] = []

    async def tool(node: _Cyclic) -> None:
        reached.append(node)

    guarded = authorize_tenant_params(tool)
    with tenant_scope("demo"):
        await guarded(node)  # matching tenant -> passes; terminates despite the cycle
    assert len(reached) == 1
    # A foreign self-referential model is still rejected.
    foreign = _Cyclic(tenant_id="other-brokerage")
    foreign.child = foreign
    reached.clear()
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await guarded(foreign)
    assert reached == []


# --- Decorator ORDER on the engine-facing write path (review round 3, Finding 1) ---------
# The engine write path is authorize_tool_ports(IdempotentVoice(RecordingVoice(raw))): tenant
# authorization must be the OUTERMOST layer, so it runs before the idempotency claim and the
# audit record. These spies prove the ordering.


class _SpyVoice:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        self.calls.append(context)
        return "call-1"

    async def get_call(self, call_id: str) -> CallRecord | None:
        return None


class _SpyIdempotencyStore:
    def __init__(self) -> None:
        self.begins: list[str] = []

    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        self.begins.append(key)
        return IdempotencyClaim(status=ClaimStatus.NEW)

    async def complete(self, key: str, result: str) -> None:
        return None


class _CollectingAudit:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


def _engine_voice(
    voice: _SpyVoice, audit: _CollectingAudit, idem: _SpyIdempotencyStore
) -> IdempotentVoice:
    # Mirrors the api wiring: authorization is applied LAST (outermost).
    return authorize_tool_ports(IdempotentVoice(RecordingVoice(voice, audit), idem), audit=audit)


async def test_authorization_is_outermost_on_the_engine_write_path() -> None:
    voice, audit, idem = _SpyVoice(), _CollectingAudit(), _SpyIdempotencyStore()
    engine_voice = _engine_voice(voice, audit, idem)
    with (
        tenant_scope("demo"),
        audit_scope(AuditContext(workflow_run_id="run-1", workflow="vapi_followup")),
    ):
        with pytest.raises(CrossTenantError):
            await engine_voice.start_outbound_call(
                "contact-1",
                "assistant-1",
                {"tenant_id": "other-brokerage", "listing_key": "L1"},
            )
    # (a) rejected before ANY store/port side effect on the write path:
    assert idem.begins == []  # the idempotency claim never ran
    assert voice.calls == []  # the raw voice adapter was never reached
    # (b)+(c) ONLY the intended security denial is recorded — no full-payload failure entry
    # from RecordingVoice landed on the normal mutation ledger:
    assert [r.integration for r in audit.records] == ["security"]
    only = audit.records[0]
    assert only.args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}
    # the call context (listing_key, and the tenant value beyond the tenant fields) never
    # leaked into any audit entry:
    assert "listing_key" not in str(only.args)


async def test_engine_write_path_passes_a_clean_call_through() -> None:
    # Authorization-outermost must not break the normal chain: a clean call still dedupes,
    # records, and reaches the adapter.
    voice, audit, idem = _SpyVoice(), _CollectingAudit(), _SpyIdempotencyStore()
    engine_voice = _engine_voice(voice, audit, idem)
    with (
        tenant_scope("demo"),
        audit_scope(AuditContext(workflow_run_id="run-1", workflow="vapi_followup")),
    ):
        call_id = await engine_voice.start_outbound_call(
            "contact-1", "assistant-1", {"listing_key": "L1"}
        )
    assert call_id == "call-1"
    assert voice.calls == [{"listing_key": "L1"}]  # reached the raw adapter
    assert idem.begins != []  # the idempotency claim ran
    assert any(r.integration == "vapi" for r in audit.records)  # recorded on the ledger
