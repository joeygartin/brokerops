"""Zero-credential identity default.

When no OIDC client is configured, the dashboard and API still need a caller.
DemoIdentityVerifier returns a fixed operator regardless of the token (or its
absence), which is what keeps `docker compose up` login-free (ADR-0007). It is
the identity analogue of DeterministicExtractor (ADR-0006): the default lives
in core, the credentialed adapter lives in an integration package.
"""

from brokerops_core.ports.identity import Principal

DEMO_PRINCIPAL = Principal(
    subject="demo-operator",
    email="operator@demo.brokerops",
    name="Demo Operator",
)


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
