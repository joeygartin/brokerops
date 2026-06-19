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
