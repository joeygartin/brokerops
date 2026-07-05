"""API proof that the uniform tool seam covers BOTH directions (BOP-011 + BOP-012).

Three guarantees:

1. **No unwrapped engine tool port, either direction.** Every tool port the workflow
   engine can reach is registered in ``app.state.engine_tool_ports`` and carries BOTH the
   authorization marker (inputs, BOP-011) and the egress-filter marker (outputs, BOP-012) —
   including the read-only MLS port and the idempotent/recording write ports. Scanning the
   whole registry (not a subset) means a new engine tool port added unwrapped in either
   direction fails the test.
2. **Rejection before data access.** A write carrying a foreign tenant id through the real
   wired store is denied before anything is persisted, and the denial lands one security
   event in the same audit ledger the operator queries.
3. **Blocking at egress.** A response that somehow carries a foreign tenant identifier out
   of the inner layers is blocked whole at the seam — the caller receives the failure, and
   the denial lands a security event on the same ledger.
"""

import asyncio
import inspect
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_api.db import InMemoryAuditLog
from brokerops_api.main import app
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.egress import (
    EGRESS_FILTERED_MARKER,
    EgressBlockedError,
    guard_tool_ports,
)
from brokerops_core.services.tenancy import CrossTenantError, tenant_scope
from brokerops_core.services.tool_authz import (
    AUTHORIZED_MARKER,
    accepts_tenant_bearing_param,
    authorize_tool_ports,
)

# Every engine-reachable tool port build_engine is handed. The enumeration below asserts
# the wiring registers exactly this set, so a port added to the engine but omitted here
# fails the test rather than silently escaping authorization.
EXPECTED_ENGINE_TOOL_PORTS = {
    "mls",
    "crm",
    "voice",
    "transaction_store",
    "feedback_store",
    "approval_repo",
    # BOP-019: the workflows' send-on-approve nodes reach the email seam
    # (its send(message) is tenant-bearing), so it must be wrapped + registered.
    "email",
}


def _has_tenant_bearing_method(port: Any) -> bool:
    """Whether ``port`` exposes at least one async method that accepts a tenant-bearing
    parameter — i.e. it is a tool entry point that could carry a foreign tenant."""
    for name in dir(port):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(port, name)
        except AttributeError:
            continue
        if inspect.iscoroutinefunction(attr) and accepts_tenant_bearing_param(attr):
            return True
    return False


def test_every_engine_tool_port_is_authorized_and_egress_filtered() -> None:
    with TestClient(app):
        registry = dict(app.state.engine_tool_ports)
    # The full engine surface is present (incl. the MLS port — Finding 2 — and the write
    # ports), so the marker checks below are not vacuously satisfied.
    assert set(registry) == EXPECTED_ENGINE_TOOL_PORTS
    # Every engine-reachable tool port — regardless of whether its params are tenant-bearing
    # today — is wrapped in BOTH directions (outermost): inputs authorized (BOP-011) and
    # outputs egress-filtered (BOP-012). Fails if any is left unwrapped in either.
    for name, port in registry.items():
        assert getattr(port, AUTHORIZED_MARKER, False) is True, (
            f"engine tool port {name!r} is not authorization-wrapped (BOP-011)"
        )
        assert getattr(port, EGRESS_FILTERED_MARKER, False) is True, (
            f"engine tool port {name!r} is not egress-filtered (BOP-012)"
        )
    # The MLS port in particular is a read-only port whose ListingQuery is not tenant-bearing
    # today; the registry still forces it through the seam, so a future tenant-bearing MLS
    # entry point cannot be added unwrapped.
    assert getattr(registry["mls"], AUTHORIZED_MARKER, False) is True
    # The email seam's egress marker is the wiring-detectable precondition BOP-020's
    # LLM-drafting gate checks before enabling an LLM output path.
    assert getattr(registry["email"], EGRESS_FILTERED_MARKER, False) is True


def test_enumeration_discovers_an_unwrapped_dict_tenant_port() -> None:
    # Negative control (Finding 1): a port whose only tenant channel is a `payload: dict`
    # is discovered by the same detector the enumeration relies on and, left unwrapped,
    # lacks the marker — so the enumeration WOULD fail for it. This closes the gap where a
    # runtime-enforced Mapping shape was invisible to the static gate.
    class _LeakyPort:
        async def act(self, payload: dict[str, str]) -> None: ...

    leaky = _LeakyPort()
    assert _has_tenant_bearing_method(leaky) is True
    assert getattr(leaky, AUTHORIZED_MARKER, False) is False
    # Wrapping it restores the guarantee.
    authorize_tool_ports(leaky)
    assert getattr(leaky, AUTHORIZED_MARKER, False) is True


def test_cross_tenant_write_is_rejected_before_persistence() -> None:
    with TestClient(app):
        store = app.state.transaction_store
        audit = app.state.audit_log

        async def attempt() -> Transaction | None:
            with tenant_scope("demo"):
                foreign = Transaction(
                    tenant_id="other-brokerage",
                    id="TXN-evil",
                    listing_key="L1",
                    stage=TransactionStage.UNDER_CONTRACT,
                    contract_date=date(2026, 1, 1),
                )
                try:
                    await store.create_transaction(foreign, [])
                except CrossTenantError:
                    pass
                # The row must not exist: authorization ran before any persistence.
                row: Transaction | None = await store.get_transaction("TXN-evil")
                return row

        persisted = asyncio.run(attempt())
        records = asyncio.run(audit.list())

    assert persisted is None
    security = [r for r in records if r.integration == "security"]
    assert len(security) == 1
    assert security[0].args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}


def test_egress_blocks_a_foreign_response_at_the_seam() -> None:
    # Defense-in-depth proof for the OUTPUT direction: the wired scoped stores already hide
    # foreign rows (so through the real app the egress scan finds nothing to block) — here a
    # deliberately leaky port stands in for a failed inner layer, guarded by the SAME
    # guard_tool_ports the api wiring applies to every engine tool port.
    class _LeakyStore:
        async def get_transaction(self, transaction_id: str) -> Transaction | None:
            return Transaction(
                tenant_id="other-brokerage",
                id="TXN-leak",
                listing_key="L1",
                stage=TransactionStage.UNDER_CONTRACT,
                contract_date=date(2026, 1, 1),
            )

    audit = InMemoryAuditLog()
    store = guard_tool_ports(_LeakyStore(), audit=audit)
    assert getattr(store, AUTHORIZED_MARKER, False) is True
    assert getattr(store, EGRESS_FILTERED_MARKER, False) is True

    async def attempt() -> None:
        with tenant_scope("demo"):
            with pytest.raises(EgressBlockedError):
                await store.get_transaction("TXN-leak")

    asyncio.run(attempt())
    records = asyncio.run(audit.list())
    # The whole response was blocked (the caller got the failure above, never the row) and
    # the denial landed one security event on the same ledger the operator queries.
    security = [r for r in records if r.integration == "security"]
    assert len(security) == 1
    assert security[0].args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}
    assert security[0].tool == "get_transaction"
