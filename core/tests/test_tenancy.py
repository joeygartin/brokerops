"""Unit coverage for the tenant-scoping seam (BOP-006).

Storage-agnostic: the seam carries the binding + enforcement, so both engines and
both store backends inherit identical confinement by construction.
"""

import pytest

from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.services.tenancy import (
    CrossTenantError,
    TenantContextMissing,
    current_tenant,
    enforce_tenant,
    record_cross_tenant_attempt,
    require_tenant,
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


def test_no_tenant_bound_is_fail_closed() -> None:
    assert current_tenant() is None
    with pytest.raises(TenantContextMissing):
        require_tenant()


def test_scope_binds_and_resets() -> None:
    with tenant_scope("demo"):
        assert require_tenant() == "demo"
        assert current_tenant() == "demo"
    assert current_tenant() is None


def test_empty_tenant_is_rejected() -> None:
    with pytest.raises(ValueError):
        with tenant_scope(""):
            pass


def test_nested_same_tenant_is_reentrant() -> None:
    with tenant_scope("demo"):
        with tenant_scope("demo"):
            assert require_tenant() == "demo"
        # inner exit leaves the outer binding intact
        assert require_tenant() == "demo"


def test_nested_different_tenant_is_rejected() -> None:
    # A prompt-injected node trying to switch lanes mid-run cannot re-bind.
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError) as exc:
            with tenant_scope("evil-brokerage"):
                pass
        assert exc.value.attempted == "evil-brokerage"
        assert exc.value.bound == "demo"
        # the outer binding is unchanged after the rejected attempt
        assert require_tenant() == "demo"


def test_enforce_tenant_stamps_ambient_for_empty_claim() -> None:
    with tenant_scope("demo"):
        assert enforce_tenant(None) == "demo"
        assert enforce_tenant("") == "demo"
        assert enforce_tenant("demo") == "demo"


def test_enforce_tenant_rejects_foreign_claim() -> None:
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError) as exc:
            enforce_tenant("other-brokerage")
        assert exc.value.attempted == "other-brokerage"
        assert exc.value.bound == "demo"


def test_enforce_tenant_fail_closed_without_scope() -> None:
    with pytest.raises(TenantContextMissing):
        enforce_tenant("anything")


@pytest.mark.asyncio
async def test_cross_tenant_attempt_is_audited() -> None:
    audit = CollectingAuditLog()
    with tenant_scope("demo"):
        try:
            enforce_tenant("other-brokerage")
        except CrossTenantError as err:
            await record_cross_tenant_attempt(audit, err, tool="create_transaction")
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.integration == "security"
    assert record.tool == "create_transaction"
    assert record.outcome is MutationOutcome.FAILURE
    assert record.args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}
