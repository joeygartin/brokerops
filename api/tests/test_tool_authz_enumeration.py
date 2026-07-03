"""API proof that tool-input authorization is applied uniformly (BOP-011).

Two guarantees:

1. **No unwrapped tool entry point.** Every tool port wired on ``app.state`` that can be
   handed a tenant-bearing parameter is authorization-wrapped. The test discovers such
   ports by introspection, so a new tool entry point added without the wrapper fails it.
2. **Rejection before data access.** A write carrying a foreign tenant id through the real
   wired store is denied before anything is persisted, and the denial lands one security
   event in the same audit ledger the operator queries.
"""

import asyncio
import inspect
from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from brokerops_api.main import app
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.tenancy import CrossTenantError, tenant_scope
from brokerops_core.services.tool_authz import AUTHORIZED_MARKER, accepts_tenant_bearing_param


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


def test_no_tenant_bearing_tool_port_is_unwrapped() -> None:
    with TestClient(app):
        # The workflow engine is the orchestrator that *calls* tools, not a tool port; it
        # is gated at each tool it reaches, not wrapped itself.
        engine = app.state.workflow_engine
        wrapped_ports: list[str] = []
        for name, value in app.state._state.items():
            if value is engine:
                continue
            if _has_tenant_bearing_method(value):
                assert getattr(value, AUTHORIZED_MARKER, False) is True, (
                    f"tool port {name!r} accepts a tenant-bearing parameter but is not "
                    f"authorization-wrapped (BOP-011)"
                )
                wrapped_ports.append(name)
    # Positive control: the three tenant-bearing stores were actually discovered, so the
    # assertion above is not vacuously satisfied.
    assert {"transaction_store", "feedback_store", "approval_repo"} <= set(wrapped_ports)


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
