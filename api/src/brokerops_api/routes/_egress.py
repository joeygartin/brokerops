"""Caller-role egress filtering for viewer-open HTTP read routes (BOP-040).

BOP-012 built the egress filter (``scrub_payload`` — secret-shape plus field-level
``Pii`` / ``RESTRICTED_CONTENT`` redaction) and wired it on the engine tool seam;
BOP-027 wired it on the four transaction-hub reads. This module is the ONE reusable
seam that closes the surface uniformly: a viewer-open read route declares
``scrub: ScrubDep`` and returns ``scrub(payload)``, so the response is filtered to the
*caller's* role (``recipient_role=principal.role``). An operator/admin (who meets every
field's ``min_role``) gets the same object back untouched; a viewer receives an object's
non-restricted fields but never one its role isn't entitled to — a message body, an
approval's draft payload, a contact email, a call transcript.

Forget-proofing mirrors BOP-011/012's port enumeration: a new read route cannot quietly
ship a restricted field unfiltered. ``response_exposes_restricted_content`` statically
decides whether a response model carries a ``Pii``-marked field (directly or nested), and
the enumeration test (``api/tests/test_egress_route_coverage.py``) walks every GET route
and asserts each whose response model does is wired to ``role_scrubber``.
"""

from typing import Annotated, Any, Protocol, TypeVar, cast, get_args

from fastapi import Depends
from pydantic import BaseModel

from brokerops_api.deps import get_current_principal
from brokerops_core.models.sensitivity import Pii
from brokerops_core.ports.identity import Principal
from brokerops_core.services.egress import scrub_payload

T = TypeVar("T")


class RoleScrubber(Protocol):
    """A caller-role-bound egress filter: redacts a payload for the caller's role.

    The generic ``__call__`` preserves the payload's type end to end, so a route's
    ``scrub(message)`` stays a ``Message`` for the type checker and the response model.
    """

    def __call__(self, payload: T) -> T: ...


def role_scrubber(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RoleScrubber:
    """Dependency: the egress filter bound to the authenticated caller's role.

    One definition, applied uniformly — a read route depends on ``ScrubDep`` and returns
    ``scrub(payload)`` instead of scrubbing ad hoc. ``scrub_payload`` is pure and
    copy-on-write, so an operator/admin gets the same object back untouched while a viewer
    gets the restricted fields redacted in place.
    """

    def scrub(payload: T) -> T:
        # scrub_payload is copy-on-write and type-preserving (it returns the same shape it
        # was given); it's typed ``Any`` because it walks arbitrary nested structures, so
        # re-assert the caller's payload type at this boundary.
        return cast(T, scrub_payload(payload, recipient_role=principal.role))

    return scrub


# The one dependency every viewer-open read route wires. Its presence in a route's
# dependency tree is the detectable marker the coverage enumeration test asserts.
ScrubDep = Annotated[RoleScrubber, Depends(role_scrubber)]


def response_exposes_restricted_content(annotation: Any, _seen: set[type] | None = None) -> bool:
    """Whether a route's response annotation carries a ``Pii``-marked field.

    Recurses Pydantic models (each field, and each field's own annotation) and generic
    containers (``list[...]``, ``Foo | None``, ...) so a restricted field nested anywhere
    under the response shape is found — the same surface ``scrub_payload`` would redact.
    A ``_seen`` set guards a self-referential model. Used by the coverage enumeration test
    to require the scrubber exactly where a viewer could otherwise receive a restricted
    field.
    """
    seen = _seen if _seen is not None else set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        for field in annotation.model_fields.values():
            if any(isinstance(meta, Pii) for meta in field.metadata):
                return True
            if response_exposes_restricted_content(field.annotation, seen):
                return True
        return False
    return any(response_exposes_restricted_content(arg, seen) for arg in get_args(annotation))
