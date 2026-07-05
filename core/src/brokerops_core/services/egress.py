"""Output/egress filtering: scan every tool response before it leaves the boundary.

Capability-security increment 3 (BOP-012) — the OUTPUT half of the uniform tool seam.
BOP-006 confines the stores, BOP-011 authorizes every tenant-bearing tool *input*; this
module scans what comes *back*, so even a response produced by a failed inner layer (or a
future tool that bypasses the scoped stores) cannot carry another tenant's data, a secret,
or role-restricted PII out of the boundary. Deterministic and framework-free: pure
pattern/annotation rules, no LLM, no external DLP dependency.

Three rule classes, all data rather than per-tool code:

1. **Cross-tenant identifier ⇒ BLOCK (fail closed).** The response's tenant surface is
   extracted with the SAME ``tenant_values`` the input side enforces (one definition, both
   directions) and checked with ``enforce_tenant``. A foreign id raises
   ``EgressBlockedError`` — the caller receives the failure, never a partial payload — and
   the denial is logged (tool + finding class only, never the payload) and recorded as a
   ``security`` event through the existing ledger seam.
2. **Secret-shaped value ⇒ REDACT in place.** Two data tables: the audit trail's
   secret-key hints (``is_secret_key`` — the same knowledge ``redact()`` persists with)
   applied to model field names and mapping keys, plus ``SECRET_VALUE_PATTERNS`` for
   value *shapes* (connection strings with embedded credentials, bearer credentials,
   private-key blocks, well-known API-key prefixes, JWT-shaped triplets) inside strings.
3. **Role-restricted PII ⇒ REDACT in place.** Fields annotated with a
   ``models.sensitivity.Pii`` marker are redacted when the recipient's role does not meet
   the field's ``min_role`` — the rule rides on the Pydantic response model that owns the
   field, never on the tool.

Wiring: ``guard_tool_ports`` is the one both-directions wrapper — it applies BOP-011's
``authorize_tool_ports`` and then wraps every async method so its RESULT is filtered
before returning. Applied to every entry in ``app.state.engine_tool_ports`` (the same
uniform seam), and the enumeration test asserts both markers on every registered port, so
no tool entry point can return unfiltered output. ``EGRESS_FILTERED_MARKER`` is the
detectable presence of the filter: wiring guards that must refuse to run without egress
filtering on a seam (BOP-020's LLM drafting gate) check ``getattr(port,
EGRESS_FILTERED_MARKER, False)`` — the exact mechanic of BOP-011's ``AUTHORIZED_MARKER``.

The engine seam's recipient is the agent, wired as ``Role.OPERATOR``: the agent *acts*
(drafts comms, places calls) but never *decides*, matching ADR-0009's operator tier — so
contact-reach PII (``min_role=OPERATOR``) stays available to the workflows that legitimately
need it, while any admin-only field and every secret shape is filtered even from the agent.
A viewer-facing surface passes ``Role.VIEWER`` and gets contact PII redacted too.
"""

import functools
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from brokerops_core.models.role import Role
from brokerops_core.models.sensitivity import Pii
from brokerops_core.ports.audit import AuditLog
from brokerops_core.services.audit import is_secret_key
from brokerops_core.services.tenancy import (
    CrossTenantError,
    enforce_tenant,
    record_cross_tenant_attempt,
)
from brokerops_core.services.tool_authz import authorize_tool_ports, tenant_values

logger = logging.getLogger(__name__)

# Marker set on a filtered callable / port so coverage is *detectable*: the enumeration
# test proves every engine tool port carries it, and wiring-time guards (BOP-020) refuse
# to enable LLM output paths on a seam that lacks it.
EGRESS_FILTERED_MARKER = "__egress_filtered__"

# In-place redaction markers. Distinct from the audit trail's "[redacted]" so a marker in
# a payload is attributable to the egress boundary, and per finding class for triage.
SECRET_REDACTED = "[redacted:secret]"
PII_REDACTED = "[redacted:pii]"

# Finding classes — what gets logged (never the payload).
FINDING_CROSS_TENANT = "cross_tenant_identifier"
FINDING_SECRET = "secret_shape"
FINDING_PII = "restricted_pii"

# Secret value *shapes* redacted inside free text (rule class 2). Deterministic and
# deliberately tight — each pattern needs a credential-specific anchor (a scheme with an
# embedded password, a vendor key prefix, a PEM header), never bare entropy, so listing
# remarks / transcripts / drafted bodies are not mangled on false positives.
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM-style private key blocks.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    # URL with embedded credentials (connection strings: postgres://user:pass@host/...).
    re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^@\s]+@"),
    # Bearer credentials ("Bearer <long-token>").
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    # Well-known API-key prefixes (Stripe/OpenAI sk-, AWS AKIA, GitHub gh*_, Slack xox*).
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # JWT-shaped triplets (three base64url segments, header starting with '{"').
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)

T = TypeVar("T")
R = TypeVar("R")


class EgressBlockedError(CrossTenantError):
    """A tool response carrying a foreign tenant identifier was blocked at egress.

    Subclasses ``CrossTenantError`` (the existing taxonomy — no parallel exception model),
    so it flows through ``record_cross_tenant_attempt`` and any handler that already
    understands cross-tenant denials, while staying distinguishable as an *output-side*
    block.
    """


def _pii_rule(field: FieldInfo) -> Pii | None:
    """The ``Pii`` marker attached to a field via ``Annotated``, if any."""
    for meta in field.metadata:
        if isinstance(meta, Pii):
            return meta
    return None


def _scrub_text(value: str, findings: list[str]) -> str:
    """Redact secret-shaped spans inside a string; returns the same object if clean."""
    scrubbed = value
    for pattern in SECRET_VALUE_PATTERNS:
        scrubbed = pattern.sub(SECRET_REDACTED, scrubbed)
    if scrubbed == value:
        return value
    findings.append(FINDING_SECRET)
    return scrubbed


def scrub_payload(
    value: Any,
    *,
    recipient_role: Role,
    findings: list[str] | None = None,
    _stack: set[int] | None = None,
) -> Any:
    """Deterministically redact secret shapes and role-restricted PII in ``value``.

    Pure and copy-on-write: the input is never mutated, and the SAME object comes back
    when nothing needed redaction (so a clean response costs no copy). Recurses models,
    mappings, and list/tuple containers; ``_stack`` guards a self-referential structure
    (a revisit on the current recursion path returns as-is instead of looping). Each
    redaction appends its finding class to ``findings`` so the caller can log tool +
    finding class — never the payload.
    """
    if findings is None:
        findings = []
    if isinstance(value, (BaseModel, Mapping, list, tuple)):
        stack = _stack if _stack is not None else set()
        if id(value) in stack:  # self-referential structure: already being scrubbed above
            return value
        stack.add(id(value))
        try:
            return _scrub_container(value, recipient_role, findings, stack)
        finally:
            stack.discard(id(value))
    if isinstance(value, str):
        return _scrub_text(value, findings)
    return value


def _scrub_container(value: Any, recipient_role: Role, findings: list[str], stack: set[int]) -> Any:
    if isinstance(value, BaseModel):
        updates: dict[str, Any] = {}
        for name, field in type(value).model_fields.items():
            raw = getattr(value, name)
            # Rule 2a: a field NAMED like a secret is redacted whole (audit-trail rule set).
            if is_secret_key(name):
                if raw not in (None, "", SECRET_REDACTED):
                    updates[name] = SECRET_REDACTED
                    findings.append(FINDING_SECRET)
                continue
            # Rule 3: a Pii-annotated field the recipient's role isn't entitled to.
            rule = _pii_rule(field)
            if rule is not None and not recipient_role.allows(rule.min_role):
                if raw not in (None, "", PII_REDACTED):
                    updates[name] = PII_REDACTED if isinstance(raw, str) else None
                    findings.append(FINDING_PII)
                continue
            scrubbed = scrub_payload(
                raw, recipient_role=recipient_role, findings=findings, _stack=stack
            )
            if scrubbed is not raw:
                updates[name] = scrubbed
        # model_copy(update=) skips validation by design: markers are plain strings and
        # every other field is carried over unchanged.
        return value.model_copy(update=updates) if updates else value
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            # Rule 2a on mapping keys — the exact rule redact() applies on the audit trail.
            if is_secret_key(str(key)) and item not in (None, "", SECRET_REDACTED):
                out[key] = SECRET_REDACTED
                findings.append(FINDING_SECRET)
                changed = True
                continue
            scrubbed = scrub_payload(
                item, recipient_role=recipient_role, findings=findings, _stack=stack
            )
            out[key] = scrubbed
            changed = changed or scrubbed is not item
        return out if changed else value
    # list / tuple
    items = [
        scrub_payload(item, recipient_role=recipient_role, findings=findings, _stack=stack)
        for item in value
    ]
    if all(new is old for new, old in zip(items, value, strict=True)):
        return value
    return tuple(items) if isinstance(value, tuple) else items


async def filter_tool_response(
    tool: str,
    payload: R,
    *,
    recipient_role: Role,
    audit: AuditLog | None = None,
) -> R:
    """Filter one tool response against the active ``TenantContext`` before it egresses.

    Order is fail-closed: the cross-tenant scan runs FIRST, so a blocked response raises
    before any part of it is returned (the caller gets the failure, never a partial
    payload). The tenant surface is the input side's ``tenant_values`` verbatim — an
    empty stamped id still runs ``enforce_tenant("")``, so a response can never egress
    with no tenant bound. Clean-but-sensitive payloads then get secret/PII redaction
    (rule classes 2 and 3) with in-place markers. On any finding, only the tool name and
    finding class are logged — never the payload.
    """
    for tenant in tenant_values("result", payload):
        try:
            enforce_tenant(tenant)
        except CrossTenantError as err:
            logger.warning("egress blocked: tool=%s finding=%s", tool, FINDING_CROSS_TENANT)
            blocked = EgressBlockedError(err.attempted, err.bound)
            if audit is not None:
                await record_cross_tenant_attempt(audit, blocked, tool=tool)
            raise blocked from err
    findings: list[str] = []
    scrubbed: R = scrub_payload(payload, recipient_role=recipient_role, findings=findings)
    if findings:
        level = logging.WARNING if FINDING_SECRET in findings else logging.INFO
        logger.log(level, "egress redaction: tool=%s findings=%s", tool, sorted(set(findings)))
    return scrubbed


def filter_egress(
    func: Callable[..., Awaitable[R]],
    *,
    tool: str | None = None,
    recipient_role: Role = Role.OPERATOR,
    audit: AuditLog | None = None,
) -> Callable[..., Awaitable[R]]:
    """Decorate an async tool so its RESULT is egress-filtered before returning."""
    tool_name: str = tool or str(getattr(func, "__name__", "tool"))

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> R:
        result = await func(*args, **kwargs)
        return await filter_tool_response(
            tool_name, result, recipient_role=recipient_role, audit=audit
        )

    setattr(wrapper, EGRESS_FILTERED_MARKER, True)
    return wrapper


def filter_port_egress(
    port: T, *, recipient_role: Role = Role.OPERATOR, audit: AuditLog | None = None
) -> T:
    """Wrap every async method of a tool port with response egress filtering.

    The output-direction sibling of ``authorize_tool_ports`` (BOP-011), applied
    identically: wraps in place and returns the SAME instance (identity, ``isinstance``
    checks, and the scoped-store wiring underneath all stay valid), covers every public
    async method so a new entry point cannot return unfiltered output, and is idempotent
    (re-wrapping a marked port is a no-op). ``EGRESS_FILTERED_MARKER`` on the port is the
    detectable presence wiring guards check for.
    """
    if getattr(port, EGRESS_FILTERED_MARKER, False):
        return port
    for name in dir(port):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(port, name)
        except AttributeError:
            continue
        if inspect.iscoroutinefunction(attr):
            setattr(
                port,
                name,
                filter_egress(attr, tool=name, recipient_role=recipient_role, audit=audit),
            )
    setattr(port, EGRESS_FILTERED_MARKER, True)
    return port


def guard_tool_ports(
    port: T, *, recipient_role: Role = Role.OPERATOR, audit: AuditLog | None = None
) -> T:
    """Both directions of the uniform tool seam: input authorization + output egress.

    One wrapper call per wired port (the BOP-011 seam, extended): inputs are
    tenant-authorized before the body runs (``authorize_tool_ports``) and the result is
    egress-filtered before it returns (``filter_port_egress``, outermost). Same instance
    back, both markers set — the enumeration test asserts both on every engine tool port.
    """
    return filter_port_egress(
        authorize_tool_ports(port, audit=audit),
        recipient_role=recipient_role,
        audit=audit,
    )
