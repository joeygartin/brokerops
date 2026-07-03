"""Tool-input authorization: reject an out-of-scope tenant parameter before data access.

Capability-security increment 2 (BOP-011, ADR-0012) — the tool-input half of the tenant
seam BOP-006 built. BOP-006 confines every *store* to the bound tenant
(``scoped_stores.py``) and the database belts it (RLS). This adds the check one layer
earlier: at the tool entry point, *before* any port/store method is reached, every
tenant-bearing Pydantic parameter is authorized against the bound ``TenantContext``. An
agent that names a resource carrying another brokerage's ``tenant_id`` is rejected before
the call runs — the store is never touched.

Reuse-first: this is a decorator over the existing Pydantic tool-input boundary + the
``tenancy.py`` primitives (``enforce_tenant`` / ``CrossTenantError``). No new exception
taxonomy, no parallel authorization model, and **no second write path** — an authorized
write tool still delegates to the same scoped-store / approval + idempotency + audit chain
(BOP-002/BOP-003) it always did; this only gates entry.

Applied uniformly: ``authorize_tool_ports`` wraps every async method of a wired tool port
so a tenant-bearing argument on *any* of them is authorized. The wrapping is in place and
returns the same instance, so the scoped-store wiring (and its ``isinstance`` checks) is
preserved and the guard sits *outside* the store. An enumeration test asserts that no tool
port accepting a tenant-bearing parameter is left unwrapped, so a new tool entry point
cannot be added without the guard.
"""

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from brokerops_core.ports.audit import AuditLog
from brokerops_core.services.tenancy import (
    CrossTenantError,
    enforce_tenant,
    record_cross_tenant_attempt,
)

logger = logging.getLogger(__name__)

# The field a Pydantic model uses to carry its tenant identity (BOP-006). A parameter is
# "tenant-bearing" when it is such a model (or a sequence/optional of one).
TENANT_FIELD = "tenant_id"
# Marker set on an authorized callable / port so the enumeration test can prove coverage.
AUTHORIZED_MARKER = "__tenant_authorized__"

T = TypeVar("T")


def _tenant_values(value: Any) -> list[str]:
    """The non-empty tenant ids carried by one argument value.

    A tenant-bearing argument is a Pydantic model exposing ``tenant_id`` — or a
    list/tuple of them (e.g. the milestone batch on ``create_transaction``). An empty id
    means "use the ambient tenant" (the normal, in-scope path), so it is never treated as
    a cross-tenant attempt and is not returned here.
    """
    if isinstance(value, BaseModel):
        tenant = getattr(value, TENANT_FIELD, "")
        return [tenant] if isinstance(tenant, str) and tenant else []
    if isinstance(value, (list, tuple)):
        found: list[str] = []
        for item in value:
            found.extend(_tenant_values(item))
        return found
    return []


def _positional_param_names(func: Callable[..., Any]) -> list[str]:
    """Positional parameter names of ``func`` for labelling a rejected argument.

    Best-effort and tolerant: a method named ``list`` cannot have its signature computed
    under PEP 649 (its ``list[...]`` return annotation shadows the builtin), and such a
    method carries no tenant-bearing parameter anyway, so an empty list is a safe fallback
    (the argument is then labelled positionally).
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return []
    return [
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]


def authorize_tenant_params(
    func: Callable[..., Awaitable[T]],
    *,
    tool: str | None = None,
    audit: AuditLog | None = None,
) -> Callable[..., Awaitable[T]]:
    """Decorate an async tool so every tenant-bearing parameter is authorized first.

    Before the wrapped call runs — and so before any port/store method it would reach —
    each argument that carries a ``tenant_id`` is checked with ``enforce_tenant``. A value
    disagreeing with the bound tenant raises ``CrossTenantError`` (the existing taxonomy)
    and the wrapped function is never entered. The rejection logs only the tool and
    parameter names, never the payload, so no tenant data, PII, or secret can leak to the
    log. When an ``audit`` log is supplied the denial is also recorded as a ``security``
    event through the existing seam (``record_cross_tenant_attempt``) — the same ledger
    BOP-006 writes to, not a second path — so short-circuiting the scoped store does not
    lose the security-event trail.
    """
    param_names = _positional_param_names(func)
    tool_name: str = tool or str(getattr(func, "__name__", "tool"))

    async def _authorize(param: str, value: Any) -> None:
        for tenant in _tenant_values(value):
            try:
                enforce_tenant(tenant)
            except CrossTenantError as err:
                # Name the tool + parameter for triage; never the payload (BOP-011).
                logger.warning(
                    "cross-tenant tool parameter rejected: tool=%s param=%s", tool_name, param
                )
                if audit is not None:
                    await record_cross_tenant_attempt(audit, err, tool=tool_name)
                raise

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        for index, value in enumerate(args):
            await _authorize(
                param_names[index] if index < len(param_names) else f"arg{index}", value
            )
        for name, value in kwargs.items():
            await _authorize(name, value)
        return await func(*args, **kwargs)

    setattr(wrapper, AUTHORIZED_MARKER, True)
    return wrapper


def authorize_tool_ports(port: T, *, audit: AuditLog | None = None) -> T:
    """Wrap every async method of a tool port with tenant-parameter authorization.

    Wraps in place and returns the *same* instance, so the port keeps its identity and
    type — the scoped-store wiring and the ``isinstance`` checks that assert it stay valid,
    and the guard sits outside the (scoped) store rather than replacing it. Read/no-model
    methods are wrapped too but are a pass-through; only a call carrying a tenant-bearing
    model is ever gated. A supplied ``audit`` log routes a denial to the security ledger.
    Idempotent: re-wrapping an already-authorized port is a no-op.
    """
    if getattr(port, AUTHORIZED_MARKER, False):
        return port
    for name in dir(port):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(port, name)
        except AttributeError:
            continue
        if inspect.iscoroutinefunction(attr):
            setattr(port, name, authorize_tenant_params(attr, tool=name, audit=audit))
    setattr(port, AUTHORIZED_MARKER, True)
    return port


def annotation_is_tenant_bearing(annotation: Any) -> bool:
    """Whether a type annotation is (or wraps) a tenant-bearing Pydantic model.

    Recurses through generic containers/unions (``list[Milestone]``, ``X | None``) so a
    batched or optional tenant-bearing parameter is detected. A string (unresolved) or a
    non-model annotation is not tenant-bearing.
    """
    if isinstance(annotation, type):
        return issubclass(annotation, BaseModel) and TENANT_FIELD in annotation.model_fields
    return any(annotation_is_tenant_bearing(arg) for arg in get_args(annotation))


def accepts_tenant_bearing_param(func: Callable[..., Any]) -> bool:
    """Whether ``func`` declares a parameter annotated as a tenant-bearing model.

    The enumeration test uses this to decide which methods *must* be authorized: a method
    that can be handed a model carrying a ``tenant_id`` is a tool entry point that can
    carry a foreign tenant.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return any(
        annotation_is_tenant_bearing(param.annotation) for param in signature.parameters.values()
    )
