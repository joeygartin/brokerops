"""API-level proof that every request runs inside the deploy's tenant scope (BOP-006).

The unit tests in core cover the seam and the scoped wrappers; this proves the api
actually wires the scoped stores and that ``TenantScopeMiddleware`` binds the tenant
at the request boundary — a read backed by a scoped store would fail closed (500) if
it did not. It also proves a cross-tenant write through the wired store lands a
security event in the same audit ledger the operator can query.
"""

import asyncio
from datetime import date

from fastapi.testclient import TestClient

from brokerops_api.db import InMemoryTransactionStore
from brokerops_api.deps import deploy_tenant
from brokerops_api.main import app
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.scoped_stores import (
    ScopedApprovalRepo,
    ScopedFeedbackStore,
    ScopedTransactionStore,
)
from brokerops_core.services.tenancy import CrossTenantError, tenant_scope


def test_lifespan_wires_scoped_stores() -> None:
    with TestClient(app):
        assert isinstance(app.state.transaction_store, ScopedTransactionStore)
        assert isinstance(app.state.approval_repo, ScopedApprovalRepo)
        assert isinstance(app.state.feedback_store, ScopedFeedbackStore)


def test_request_is_bound_to_deploy_tenant() -> None:
    # GET reaches a scoped store; a 200 (not a 500 fail-closed) proves the middleware
    # bound a tenant for the request. deploy_tenant() defaults to "demo".
    assert deploy_tenant() == "demo"
    with TestClient(app) as client:
        resp = client.get("/transactions")
        assert resp.status_code == 200


def _txn(txn_id: str, tenant_id: str) -> Transaction:
    return Transaction(
        tenant_id=tenant_id,
        id=txn_id,
        listing_key="L1",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 1, 1),
    )


def test_admin_count_and_clear_cannot_reach_another_tenant() -> None:
    # Even on the superuser/in-memory path where RLS is inert, the admin reset/count
    # surface is confined to the bound tenant — a foreign tenant's rows are neither
    # counted nor deleted (BOP-006 r2, deletion blast radius).
    async def scenario() -> tuple[int, bool]:
        inner = InMemoryTransactionStore()
        # Seed two tenants' rows directly into the shared store.
        with tenant_scope("brokerage-a"):
            await inner.create_transaction(_txn("a1", "brokerage-a"), [])
        with tenant_scope("brokerage-b"):
            await inner.create_transaction(_txn("b1", "brokerage-b"), [])
            count_b = await inner.count_transactions()  # sees only its own row
            await inner.clear()  # deletes only its own row
        # brokerage-a's row survives brokerage-b's reset
        with tenant_scope("brokerage-a"):
            survives = await inner.get_transaction("a1") is not None
        return count_b, survives

    count_b, survives = asyncio.run(scenario())
    assert count_b == 1
    assert survives is True


def test_demo_seed_through_real_scoped_wiring() -> None:
    # The /demo/seed route reaches count_transactions/clear/create_transaction via the
    # ScopedTransactionStore the lifespan installs (no dependency override), proving the
    # admin surface works through the real wiring — the zero-credential demo still seeds.
    with TestClient(app) as client:
        first = client.post("/demo/seed")
        assert first.status_code == 200
        body = first.json()
        assert body["seeded"] is True and body["transactions"] >= 1
        # idempotent: a second seed without reset is a no-op
        assert client.post("/demo/seed").json()["seeded"] is False
        # reset re-seeds (exercises clear())
        assert client.post("/demo/seed", json={"reset": True}).json()["seeded"] is True


def test_cross_tenant_write_through_wired_store_is_audited() -> None:
    with TestClient(app):
        store = app.state.transaction_store
        audit = app.state.audit_log

        async def attempt() -> None:
            # A prompt-injected node populated tenant_id with another brokerage's id.
            with tenant_scope("demo"):
                foreign = Transaction(
                    tenant_id="other-brokerage",
                    id="tx-evil",
                    listing_key="L1",
                    stage=TransactionStage.UNDER_CONTRACT,
                    contract_date=date(2026, 1, 1),
                )
                try:
                    await store.create_transaction(foreign, [])
                except CrossTenantError:
                    pass

        asyncio.run(attempt())
        records = asyncio.run(audit.list())
    security = [r for r in records if r.integration == "security"]
    assert len(security) == 1
    assert security[0].args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}
