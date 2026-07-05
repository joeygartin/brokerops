"""Field-level sensitivity annotations — the egress PII rule set as data (BOP-012).

A response model declares which of its fields are PII and the minimum recipient role
entitled to see them by attaching a ``Pii`` marker via ``typing.Annotated``::

    email: Annotated[str | None, CONTACT_PII] = None

The egress filter (``services/egress.py``) reads these markers off ``model_fields``
metadata and redacts a marked field when the recipient's role does not meet the bar —
so the entitlement rule lives ON the model that owns the field (one rule set, applied
everywhere), never as per-tool code. Pydantic ignores unknown ``Annotated`` metadata,
so the marker changes no validation, serialization, or JSON-Schema behavior.
"""

from dataclasses import dataclass

from brokerops_core.models.role import Role


@dataclass(frozen=True)
class Pii:
    """Marks a Pydantic field as PII visible only to recipients of at least ``min_role``."""

    min_role: Role = Role.OPERATOR


# Direct-reach contact PII (email addresses, phone numbers). Operators *act* — they
# draft, approve, and send comms, so they are entitled to contact reach; viewers read
# dashboards and are not (ADR-0009's read/act split, applied to data instead of routes).
CONTACT_PII = Pii(min_role=Role.OPERATOR)
