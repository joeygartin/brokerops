"""Tenant-scoping seam — structural confinement of every data access to one tenant.

brokerops is single-tenant *per deploy* (own GCP project + database per brokerage),
so the strongest tenant boundary is the deployment itself. This seam adds the
in-code enforcement that makes that boundary explicit and attacker-proof (BOP-006):
the tenant a request acts under is bound BELOW the agent — from deploy config at the
request boundary — and carried on a ContextVar that no core service signature and no
MCP tool argument can supply or override. The agent has no "which brokerage"
parameter and can only ever reach its own.

The principle (BOP-006): the agent can ask for anything and can be tricked into
asking — security's job is to make asking insufficient. So the data layer reads the
tenant from here, never from a caller argument, and a write that carries a *foreign*
tenant id (the shape a prompt-injected escape attempt takes) is rejected via
`enforce_tenant` and recorded as a security event, not honored.

Mirrors the audit seam (``core/services/audit.py``): a ContextVar set at the
run/request boundary, read by the data layer. ContextVars copy into the child tasks
the engine spawns, so a write deep in a workflow always sees its request's tenant.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.ports.audit import AuditLog
from brokerops_core.services.audit import current_audit_context

# The integration label under which a denied cross-tenant attempt is filed in the
# action-audit ledger, so security events are queryable alongside business writes.
SECURITY_INTEGRATION = "security"


@dataclass(frozen=True)
class TenantContext:
    """The tenant a request/run is confined to, bound at the request boundary."""

    tenant_id: str


_tenant: ContextVar[TenantContext | None] = ContextVar("tenant", default=None)


class TenantContextMissing(RuntimeError):
    """Raised when a data access runs with no tenant bound — fail closed.

    A query that cannot prove which tenant it belongs to must never run; an
    unscoped read/write is the exact failure this seam exists to prevent.
    """


class CrossTenantError(PermissionError):
    """Raised when an operation is attempted under a tenant other than the bound one."""

    def __init__(self, attempted: str, bound: str) -> None:
        super().__init__(f"cross-tenant access denied: attempted {attempted!r} under {bound!r}")
        self.attempted = attempted
        self.bound = bound


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    """Bind ``tenant_id`` for the duration of a request/run; reset on exit.

    Binding is **immutable** within a scope: nesting the *same* tenant is a no-op
    (re-entrancy is fine), but nesting a *different* tenant raises
    ``CrossTenantError`` — a node that tries to switch lanes mid-run cannot. The
    tenant is always supplied by trusted boundary code (config-derived), never by
    model state.

    The re-bind rejection is hard (it raises) but **not separately audited**:
    ``tenant_scope`` is not a node-reachable surface (it is not an MCP tool or core
    service a node calls), so it is defense-in-depth, not a primary attack vector.
    The audited cross-tenant vector is the data path — a write carrying a foreign
    model ``tenant_id`` (see ``enforce_tenant`` + the scoped stores).
    """
    if not tenant_id:
        raise ValueError("tenant_id must be non-empty")
    current = _tenant.get()
    if current is not None and current.tenant_id != tenant_id:
        raise CrossTenantError(tenant_id, current.tenant_id)
    token = _tenant.set(TenantContext(tenant_id=tenant_id))
    try:
        yield
    finally:
        _tenant.reset(token)


def current_tenant() -> str | None:
    """The bound tenant id, or None if no scope is active (use in non-fatal paths)."""
    ctx = _tenant.get()
    return ctx.tenant_id if ctx else None


def require_tenant() -> str:
    """The bound tenant id; raise ``TenantContextMissing`` if none is bound (fail closed)."""
    ctx = _tenant.get()
    if ctx is None:
        raise TenantContextMissing("no tenant bound; data access requires a tenant scope")
    return ctx.tenant_id


def enforce_tenant(claimed: str | None) -> str:
    """Return the bound tenant; reject a non-empty ``claimed`` that disagrees with it.

    The data layer calls this when a persisted model carries a ``tenant_id``: a node
    can populate that field (it is attacker-influenceable the moment the agent reads
    external text), so a value that does not match the bound tenant is a cross-tenant
    attempt and raises ``CrossTenantError``. An empty/None ``claimed`` means "use the
    ambient tenant" — the normal path — and is stamped with the bound value.
    """
    bound = require_tenant()
    if claimed and claimed != bound:
        raise CrossTenantError(claimed, bound)
    return bound


async def record_cross_tenant_attempt(
    audit: AuditLog, error: CrossTenantError, *, tool: str
) -> None:
    """Record a denied cross-tenant attempt in the action-audit ledger as a security event.

    Reuses ``MutationRecord`` (BOP-002) so security denials are durable and queryable
    next to business writes. Run attribution is pulled from the audit context when one
    is active. No sensitive value is logged — only the (non-secret) tenant identifiers.
    """
    context = current_audit_context()
    record = MutationRecord(
        id=uuid4().hex,
        workflow_run_id=context.workflow_run_id if context else "",
        workflow=context.workflow if context else "",
        tool=tool,
        integration=SECURITY_INTEGRATION,
        args={"attempted_tenant": error.attempted, "bound_tenant": error.bound},
        approval_id=context.approval_id if context else None,
        actor=context.actor if context else None,
        outcome=MutationOutcome.FAILURE,
        error=str(error),
        created_at=datetime.now(UTC),
    )
    await audit.record(record)
