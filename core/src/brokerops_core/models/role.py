"""The operator role hierarchy — a domain model, importable from `models`.

Lived in ``ports/identity.py`` originally (ADR-0009); moved here (BOP-012) because
field-level sensitivity annotations (``models/sensitivity.py``) reference roles, and a
model may never import from ``ports`` (ports import models — the dependency points one
way). ``ports/identity.py`` re-exports ``Role``, so every existing import site is
unchanged.
"""

from enum import Enum


class Role(str, Enum):
    """Operator authorization level, hierarchical: admin > operator > viewer.

    viewer reads, operator also acts (start workflows, place calls), admin also
    decides human-in-the-loop approvals. Roles are assigned from a deployment's
    config (RoleResolver) — the allowlist gates *who* may sign in, the role gates
    *what* they may do.
    """

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def allows(self, required: "Role") -> bool:
        """True if this role meets or exceeds a minimum required role."""
        return self.rank >= required.rank


_ROLE_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
