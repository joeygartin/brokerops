"""Magic-link request/redeem routes + session-JWT bearer on a protected route."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brokerops_api.auth.session import SessionTokenService, SessionTokenVerifier
from brokerops_api.db import InMemoryMagicTokenStore
from brokerops_api.main import app
from brokerops_api.routes import auth as auth_route
from brokerops_core.services.identity import EmailAllowlist
from brokerops_core.services.magic_link import MagicLinkService

KEY = "magic-test-key"


class CapturingEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject, text))


client = TestClient(app)
EMAIL = CapturingEmail()


@pytest.fixture(autouse=True)
def _wire_magic() -> Iterator[None]:
    EMAIL.sent.clear()
    auth_route._recent_requests.clear()  # reset the per-email throttle between tests
    original_verifier = app.state.identity_verifier
    original_magic = app.state.magic_link_service
    # Session JWTs are the accepted bearer; magic service issues them.
    app.state.identity_verifier = SessionTokenVerifier(KEY)
    app.state.magic_link_service = MagicLinkService(
        store=InMemoryMagicTokenStore(),
        email=EMAIL,
        session_issuer=SessionTokenService(KEY),
        allowlist=EmailAllowlist(allowed_emails=frozenset({"op@acme.com"})),
        public_base_url="http://localhost:5173",
    )
    yield
    app.state.identity_verifier = original_verifier
    app.state.magic_link_service = original_magic


def _request(email: str) -> int:
    return int(client.post("/auth/magic/request", json={"email": email}).status_code)


def _link_token() -> str:
    return EMAIL.sent[-1][2].split("token=", 1)[1].split()[0]


def test_request_is_202_and_emails_allowlisted() -> None:
    assert _request("op@acme.com") == 202
    assert len(EMAIL.sent) == 1


def test_request_is_202_but_silent_for_non_allowlisted() -> None:
    assert _request("intruder@evil.com") == 202  # no enumeration
    assert EMAIL.sent == []


def test_redeem_yields_a_working_bearer() -> None:
    _request("op@acme.com")
    token = _link_token()
    redeemed = client.post("/auth/magic/redeem", json={"token": token})
    assert redeemed.status_code == 200
    session = redeemed.json()["session_token"]
    # The session JWT is accepted on a protected route.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {session}"})
    assert me.status_code == 200
    assert me.json()["email"] == "op@acme.com"


def test_redeem_is_single_use() -> None:
    _request("op@acme.com")
    token = _link_token()
    assert client.post("/auth/magic/redeem", json={"token": token}).status_code == 200
    assert client.post("/auth/magic/redeem", json={"token": token}).status_code == 401


def test_redeem_rejects_garbage() -> None:
    assert client.post("/auth/magic/redeem", json={"token": "nope"}).status_code == 401


def test_request_rate_limited() -> None:
    for _ in range(3):
        assert _request("op@acme.com") == 202
    assert _request("op@acme.com") == 429


def test_magic_routes_404_when_disabled() -> None:
    app.state.magic_link_service = None
    assert client.post("/auth/magic/request", json={"email": "op@acme.com"}).status_code == 404
    assert client.post("/auth/magic/redeem", json={"token": "x"}).status_code == 404
