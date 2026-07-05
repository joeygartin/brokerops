from typing import Protocol

from pydantic import BaseModel

# Role is a domain model (models/role.py, moved in BOP-012 so sensitivity annotations can
# reference it without a models→ports cycle); re-exported here so import sites are stable.
from brokerops_core.models.role import Role as Role


class Principal(BaseModel):
    """The authenticated identity of a dashboard operator.

    Produced by an IdentityVerifier from a bearer token; carried by the API as
    the resolved caller. The fields are the contract — how the identity is
    proven (Google OIDC, a demo stand-in, IAP later) is the adapter's concern.
    """

    subject: str
    email: str
    name: str = ""
    verified: bool = True
    # Least privilege: an identity constructed without an explicit role is read-only.
    # Every real verifier sets the role from the deployment's RoleResolver; this default
    # only catches a Principal built with no role, which must never imply write access.
    role: Role = Role.VIEWER


class AuthError(Exception):
    """Raised by a verifier when a token cannot be accepted.

    `forbidden` distinguishes "I cannot trust this token" (missing, expired, or
    malformed → 401) from "this is a valid identity that is not allowed in"
    (off the domain/email allowlist → 403). The API layer maps the flag to the
    HTTP status; core stays transport-free.
    """

    def __init__(self, message: str, *, forbidden: bool = False) -> None:
        super().__init__(message)
        self.forbidden = forbidden


class IdentityVerifier(Protocol):
    """Boundary to identity verification.

    The demo default returns a fixed operator so the stack runs with zero
    credentials (ADR-0007); a Google-OIDC adapter activates when a client id is
    configured. core/ depends only on this Protocol.
    """

    async def verify(self, token: str | None) -> Principal: ...
