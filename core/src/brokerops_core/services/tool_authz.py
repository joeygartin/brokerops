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

**Tenant-bearing surface (explicit bound).** By convention (ADR-0012) a tenant identifier
rides on a persisted Pydantic model's ``tenant_id`` field — no tool accepts a tenant as a
bare argument. This module treats an *argument* as tenant-bearing when it is:

1. a Pydantic model exposing a ``tenant_id`` field (the real, intended shape), a list/tuple
   of them (e.g. the milestone batch on ``create_transaction``), or a model that *nests*
   one of those (extraction recurses model fields, with cycle protection);
2. defensively, a bare argument *named* ``tenant_id`` / ``*_tenant_id`` (a scalar tenant
   arg the convention forbids — never rely on absence); or
3. defensively, a mapping/dict argument carrying a ``tenant_id`` key (e.g. an opaque
   ``payload: dict``).

Runtime enforcement (``_tenant_values``) and static detection
(``accepts_tenant_bearing_param``, the enumeration test's gate) cover the **same** surface,
so a shape enforced at runtime is also discovered statically — no divergence, no silent
hole. One deliberate, documented bound keeps them honest and bounded: a mapping is a tenant
channel only as a *top-level argument* (or a list of them), **not** as a field *inside* a
model — a model's dict field is business data (e.g. ``MutationRecord.args``,
``ShowingFeedback.structured_answers``), never a tenant channel, so descending it would
both mis-flag infra ports and diverge from ADR-0012. Model recursion therefore follows
nested models and their list/tuple containers but stops at a model's mapping fields, on
both the runtime and static sides.

**The empty case is not a bypass:** a model whose ``tenant_id`` is ``""`` ("use the ambient
tenant") is still tenant-bearing and still runs ``enforce_tenant("")`` — which calls
``require_tenant()`` — so an empty-stamped model can never reach a store with no
``TenantContext`` bound (fail-closed).
"""

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar, get_args, get_origin

from pydantic import BaseModel

from brokerops_core.ports.audit import AuditLog
from brokerops_core.services.tenancy import (
    CrossTenantError,
    enforce_tenant,
    record_cross_tenant_attempt,
)

logger = logging.getLogger(__name__)

# The field a Pydantic model uses to carry its tenant identity (BOP-006).
TENANT_FIELD = "tenant_id"
# Marker set on an authorized callable / port so the enumeration test can prove coverage.
AUTHORIZED_MARKER = "__tenant_authorized__"

T = TypeVar("T")


def _is_tenant_param_name(name: str) -> bool:
    """Whether a parameter name denotes a bare tenant identifier (ADR-0012 forbids these,
    so this is a defensive belt, not the intended shape)."""
    return name == TENANT_FIELD or name.endswith(f"_{TENANT_FIELD}")


def _tenant_values(
    param_name: str, value: Any, *, within_model: bool = False, seen: set[int] | None = None
) -> list[str]:
    """The tenant ids an argument commits the call to, each of which must be authorized.

    Recurses models, their list/tuple fields, and list/tuple/mapping arguments; ``seen``
    guards against a self-referential model looping forever. Includes empty ids: a model
    whose ``tenant_id`` is ``""`` still commits the call to the *ambient* tenant, so it must
    run ``enforce_tenant("")`` (which asserts a tenant is bound) — dropping it would let an
    empty-stamped model reach a store fail-open.

    ``within_model`` marks recursion *inside* a model: a mapping reached there is a business
    field (``MutationRecord.args`` etc.), never a tenant channel, so it is not descended —
    keeping this consistent with the static detector (see the module docstring).
    """
    if seen is None:
        seen = set()
    if isinstance(value, (BaseModel, list, tuple)):
        if id(value) in seen:  # cycle / repeat guard
            return []
        seen.add(id(value))
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        found: list[str] = []
        if TENANT_FIELD in fields:
            tenant = getattr(value, TENANT_FIELD, "")
            found.append(tenant if isinstance(tenant, str) else "")
        for name in fields:
            if name == TENANT_FIELD:
                continue
            field_value = getattr(value, name, None)
            if isinstance(field_value, (BaseModel, list, tuple)):
                found.extend(_tenant_values(name, field_value, within_model=True, seen=seen))
        return found
    if isinstance(value, Mapping):
        if within_model:
            return []
        tenant = value.get(TENANT_FIELD)
        return [tenant] if isinstance(tenant, str) else []
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_tenant_values(param_name, item, within_model=within_model, seen=seen))
        return found
    if _is_tenant_param_name(param_name) and isinstance(value, str):
        return [value]
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
        for tenant in _tenant_values(param, value):
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
    and the guard sits outside the (scoped) store rather than replacing it. Methods with no
    tenant-bearing argument are wrapped too but pass straight through; a call carrying a
    tenant-bearing argument runs ``enforce_tenant`` first (fail-closed even for an empty
    ambient tenant). A supplied ``audit`` log routes a denial to the security ledger.
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


def _annotation_is_mapping(annotation: Any) -> bool:
    """Whether an annotation is (or wraps, via a union/optional) a mapping/dict type — the
    static mirror of the runtime "top-level mapping argument" tenant channel."""
    if isinstance(annotation, type):
        return issubclass(annotation, Mapping)
    origin = get_origin(annotation)
    if isinstance(origin, type) and issubclass(origin, Mapping):
        return True
    return any(_annotation_is_mapping(arg) for arg in get_args(annotation))


def _annotation_nests_tenant_model(annotation: Any, seen: set[type] | None = None) -> bool:
    """Whether an annotation is, contains, or nests a Pydantic model with a ``tenant_id``.

    Recurses model fields and list/tuple/union containers (``seen`` guards a self-
    referential model) but, mirroring the runtime rule, does **not** descend a model's
    mapping field — a dict field is business data, not a tenant channel.
    """
    if seen is None:
        seen = set()
    if isinstance(annotation, type):
        if not issubclass(annotation, BaseModel):
            return False
        if annotation in seen:
            return False
        seen.add(annotation)
        if TENANT_FIELD in annotation.model_fields:
            return True
        return any(
            _annotation_nests_tenant_model(field.annotation, seen)
            for field in annotation.model_fields.values()
        )
    origin = get_origin(annotation)
    if isinstance(origin, type) and issubclass(origin, Mapping):
        return False  # a mapping field/annotation is not descended (matches runtime)
    return any(_annotation_nests_tenant_model(arg, seen) for arg in get_args(annotation))


def annotation_is_tenant_bearing(annotation: Any) -> bool:
    """Whether a type annotation denotes a tenant-bearing argument.

    True when it is/nests a ``tenant_id``-bearing model (``list[Milestone]``, ``X | None``,
    a model nesting one) or is a top-level mapping/dict (which can smuggle a ``tenant_id``
    key). This is the static mirror of ``_tenant_values``. A plain string or non-tenant
    model annotation is not tenant-bearing.
    """
    return _annotation_nests_tenant_model(annotation) or _annotation_is_mapping(annotation)


def accepts_tenant_bearing_param(func: Callable[..., Any]) -> bool:
    """Whether ``func`` declares a statically tenant-bearing parameter.

    The enumeration test uses this to decide which methods *must* be authorized. It flags a
    parameter *named* ``tenant_id`` / ``*_tenant_id``, one annotated as a tenant-bearing
    model (or a container/nesting of one), or one annotated as a mapping/dict — the same
    surface ``_tenant_values`` enforces at runtime, so the static gate is never blind to a
    shape the runtime accepts.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return any(
        _is_tenant_param_name(name) or annotation_is_tenant_bearing(param.annotation)
        for name, param in signature.parameters.items()
    )
