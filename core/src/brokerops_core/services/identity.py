"""Zero-credential identity default.

When no OIDC client is configured, the dashboard and API still need a caller.
DemoIdentityVerifier returns a fixed operator regardless of the token (or its
absence), which is what keeps `docker compose up` login-free (ADR-0007). It is
the identity analogue of DeterministicExtractor (ADR-0006): the default lives
in core, the credentialed adapter lives in an integration package.
"""

from brokerops_core.ports.identity import Principal, Role

DEMO_PRINCIPAL = Principal(
    subject="demo-operator",
    email="operator@demo.brokerops",
    name="Demo Operator",
    role=Role.ADMIN,  # the login-free demo operator can do everything
)


def _lower_set(emails: frozenset[str] | None) -> frozenset[str] | None:
    if not emails:
        return None
    cleaned = frozenset(e.strip().lower() for e in emails if e.strip())
    return cleaned or None


class DemoIdentityVerifier:
    """Accepts any caller as the single demo operator."""

    async def verify(self, token: str | None) -> Principal:
        return DEMO_PRINCIPAL


class EmailAllowlist:
    """Who may sign in, by Workspace domain and/or explicit email.

    Shared by every login method (Google OIDC verifies it at token time, magic
    link at request and redeem time) so a deployment's access list lives in one
    place. With neither a domain nor an email set, any verified address is
    permitted — the method itself is the gate.
    """

    def __init__(
        self,
        allowed_domain: str | None = None,
        allowed_emails: frozenset[str] | None = None,
    ) -> None:
        self._allowed_domain = allowed_domain or None
        # Compare case-insensitively; a hand-edited list may not be lowercased.
        self._allowed_emails = (
            frozenset(e.strip().lower() for e in allowed_emails if e.strip())
            if allowed_emails
            else None
        )

    @property
    def unrestricted(self) -> bool:
        return self._allowed_emails is None and self._allowed_domain is None

    def permits(self, email: str, hosted_domain: object = None) -> bool:
        email = email.lower()
        if self.unrestricted:
            return True
        if self._allowed_emails is not None and email in self._allowed_emails:
            return True
        if self._allowed_domain is not None:
            domain = email.rpartition("@")[2]
            if hosted_domain == self._allowed_domain or domain == self._allowed_domain:
                return True
        return False


class RoleResolver:
    """Maps an already-allowlisted email to its Role from deployment config.

    Parallel to EmailAllowlist: the allowlist gates *who* may sign in, this gates
    *what* they may do. With no admin or viewer rule configured the resolver is
    unrestricted and grants ADMIN to everyone — so enabling auth without role
    config behaves exactly as before RBAC (a flat operator list). Once any rule is
    set, an email matching an admin rule is ADMIN, a viewer rule is VIEWER, and any
    other allowlisted email defaults to OPERATOR. Admin takes precedence.
    """

    def __init__(
        self,
        admin_emails: frozenset[str] | None = None,
        admin_domain: str | None = None,
        viewer_emails: frozenset[str] | None = None,
        viewer_domain: str | None = None,
    ) -> None:
        self._admin_emails = _lower_set(admin_emails)
        self._admin_domain = admin_domain or None
        self._viewer_emails = _lower_set(viewer_emails)
        self._viewer_domain = viewer_domain or None

    @property
    def unrestricted(self) -> bool:
        return (
            self._admin_emails is None
            and self._admin_domain is None
            and self._viewer_emails is None
            and self._viewer_domain is None
        )

    def role_for(self, email: str, hosted_domain: object = None) -> Role:
        if self.unrestricted:
            return Role.ADMIN
        if self._matches(email, hosted_domain, self._admin_emails, self._admin_domain):
            return Role.ADMIN
        if self._matches(email, hosted_domain, self._viewer_emails, self._viewer_domain):
            return Role.VIEWER
        return Role.OPERATOR

    @staticmethod
    def _matches(
        email: str,
        hosted_domain: object,
        emails: frozenset[str] | None,
        domain: str | None,
    ) -> bool:
        email = email.lower()
        if emails is not None and email in emails:
            return True
        if domain is not None:
            if hosted_domain == domain or email.rpartition("@")[2] == domain:
                return True
        return False
