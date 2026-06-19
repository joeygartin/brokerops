"""Auth at the API edge, both modes.

Demo mode (no OIDC client): protected routes resolve the demo operator with no
token. Enabled mode (a verifier stand-in): the bearer is required and mapped to
401/403. We override the verifier via app.state rather than spin up real Google.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brokerops_api.deps import get_approval_repo
from brokerops_api.db import InMemoryApprovalRepo
from brokerops_api.main import app
from brokerops_core.ports.identity import AuthError, Principal
from brokerops_core.services.identity import DemoIdentityVerifier

client = TestClient(app)

DEMO_VERIFIER = DemoIdentityVerifier()


class StubGoogleVerifier:
    """Stands in for GoogleOIDCVerifier: 'good' passes, anything else 401s."""

    async def verify(self, token: str | None) -> Principal:
        if token == "good":
            return Principal(subject="42", email="agent@acme.com", name="Agent")
        if token == "forbidden":
            raise AuthError("not allowed", forbidden=True)
        raise AuthError("bad token")


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    original = app.state.identity_verifier
    app.dependency_overrides[get_approval_repo] = lambda: InMemoryApprovalRepo()
    yield
    app.state.identity_verifier = original
    app.dependency_overrides.clear()


def _use(verifier: object) -> None:
    app.state.identity_verifier = verifier


# ── demo mode ──────────────────────────────────────────────────────────


def test_config_reports_disabled_in_demo() -> None:
    _use(DEMO_VERIFIER)
    body = client.get("/auth/config").json()
    assert body == {"enabled": False, "methods": [], "client_id": None}


def test_protected_route_open_in_demo() -> None:
    _use(DEMO_VERIFIER)
    assert client.get("/approvals").status_code == 200


def test_me_returns_demo_operator() -> None:
    _use(DEMO_VERIFIER)
    assert client.get("/auth/me").json()["email"] == "operator@demo.brokerops"


# ── enabled mode ───────────────────────────────────────────────────────


def test_config_reports_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OIDC_CLIENT_ID", "client-xyz")
    _use(StubGoogleVerifier())
    body = client.get("/auth/config").json()
    assert body == {"enabled": True, "methods": ["google"], "client_id": "client-xyz"}


def test_protected_route_401_without_token() -> None:
    _use(StubGoogleVerifier())
    assert client.get("/approvals").status_code == 401


def test_protected_route_200_with_valid_bearer() -> None:
    _use(StubGoogleVerifier())
    resp = client.get("/approvals", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200


def test_forbidden_identity_is_403() -> None:
    _use(StubGoogleVerifier())
    resp = client.get("/approvals", headers={"Authorization": "Bearer forbidden"})
    assert resp.status_code == 403


def test_me_echoes_authenticated_principal() -> None:
    _use(StubGoogleVerifier())
    resp = client.get("/auth/me", headers={"Authorization": "Bearer good"})
    assert resp.json()["email"] == "agent@acme.com"


def test_public_endpoints_stay_open() -> None:
    _use(StubGoogleVerifier())
    assert client.get("/readyz").status_code == 200
    assert client.get("/auth/config").status_code == 200
