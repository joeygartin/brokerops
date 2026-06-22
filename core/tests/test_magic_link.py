"""MagicLinkService logic over in-memory fakes — no I/O, no framework."""

from datetime import UTC, datetime, timedelta

import pytest

from brokerops_core.ports.auth import ConsumedToken
from brokerops_core.ports.identity import AuthError, Principal
from brokerops_core.services.email import ConsoleEmailSender
from brokerops_core.ports.identity import Role
from brokerops_core.services.identity import EmailAllowlist, RoleResolver
from brokerops_core.services.magic_link import MagicLinkService


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, ConsumedToken] = {}
        self.consumed: set[str] = set()

    async def create(self, token_hash: str, email: str, expires_at: datetime) -> None:
        self.rows[token_hash] = ConsumedToken(email=email, expires_at=expires_at)

    async def consume(self, token_hash: str) -> ConsumedToken | None:
        if token_hash not in self.rows or token_hash in self.consumed:
            return None
        self.consumed.add(token_hash)
        return self.rows[token_hash]


class CapturingEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject, text))


class StubIssuer:
    def issue(self, principal: Principal) -> str:
        return f"session-for-{principal.email}"


def _service(
    *,
    allowlist: EmailAllowlist,
    email: CapturingEmail | None = None,
    store: FakeStore | None = None,
) -> tuple[MagicLinkService, FakeStore, CapturingEmail]:
    store = store or FakeStore()
    email = email or CapturingEmail()
    svc = MagicLinkService(
        store=store,
        email=email,
        session_issuer=StubIssuer(),
        allowlist=allowlist,
        public_base_url="https://app.example.com/",
    )
    return svc, store, email


def _link_token(text: str) -> str:
    return text.split("token=", 1)[1].split()[0]


async def test_request_emails_allowlisted_address() -> None:
    svc, store, email = _service(allowlist=EmailAllowlist(allowed_emails=frozenset({"a@x.com"})))
    await svc.request("A@x.com")
    assert len(email.sent) == 1
    to, subject, text = email.sent[0]
    assert to == "a@x.com"
    assert "https://app.example.com/auth/callback?token=" in text
    assert len(store.rows) == 1  # stored by hash


async def test_request_silently_drops_non_allowlisted() -> None:
    svc, store, email = _service(allowlist=EmailAllowlist(allowed_emails=frozenset({"a@x.com"})))
    await svc.request("intruder@y.com")
    assert email.sent == []
    assert store.rows == {}


async def test_redeem_round_trip_issues_session() -> None:
    svc, _store, email = _service(allowlist=EmailAllowlist())
    await svc.request("user@any.com")
    token = _link_token(email.sent[0][2])
    assert await svc.redeem(token) == "session-for-user@any.com"


async def test_redeem_is_single_use() -> None:
    svc, _store, email = _service(allowlist=EmailAllowlist())
    await svc.request("user@any.com")
    token = _link_token(email.sent[0][2])
    await svc.redeem(token)
    with pytest.raises(AuthError) as exc:
        await svc.redeem(token)
    assert exc.value.forbidden is False


async def test_redeem_rejects_unknown_token() -> None:
    svc, _store, _email = _service(allowlist=EmailAllowlist())
    with pytest.raises(AuthError):
        await svc.redeem("never-issued")


async def test_redeem_rejects_expired_token() -> None:
    store = FakeStore()
    svc, _store, email = _service(allowlist=EmailAllowlist(), store=store)
    await svc.request("user@any.com")
    # Backdate the stored expiry so the token is already stale.
    (h,) = store.rows
    store.rows[h] = ConsumedToken(
        email="user@any.com", expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    token = _link_token(email.sent[0][2])
    with pytest.raises(AuthError):
        await svc.redeem(token)


async def test_redeem_forbids_email_dropped_from_allowlist() -> None:
    # Token minted while unrestricted, then the allowlist tightens before redeem.
    store = FakeStore()
    open_svc, _s, email = _service(allowlist=EmailAllowlist(), store=store)
    await open_svc.request("user@any.com")
    token = _link_token(email.sent[0][2])
    tight, _s2, _e2 = _service(
        allowlist=EmailAllowlist(allowed_emails=frozenset({"someone@else.com"})), store=store
    )
    with pytest.raises(AuthError) as exc:
        await tight.redeem(token)
    assert exc.value.forbidden is True


class CapturingIssuer:
    def __init__(self) -> None:
        self.principal: Principal | None = None

    def issue(self, principal: Principal) -> str:
        self.principal = principal
        return "session"


async def test_redeem_stamps_resolved_role() -> None:
    issuer = CapturingIssuer()
    svc = MagicLinkService(
        store=FakeStore(),
        email=(email := CapturingEmail()),
        session_issuer=issuer,
        allowlist=EmailAllowlist(),
        roles=RoleResolver(
            admin_emails=frozenset({"boss@acme.com"}),
            viewer_emails=frozenset({"auditor@acme.com"}),
        ),
        public_base_url="https://app.example.com",
    )
    for address, expected in [
        ("boss@acme.com", Role.ADMIN),
        ("auditor@acme.com", Role.VIEWER),
        ("agent@acme.com", Role.OPERATOR),
    ]:
        await svc.request(address)
        await svc.redeem(_link_token(email.sent[-1][2]))
        assert issuer.principal is not None
        assert issuer.principal.role is expected


async def test_console_sender_does_not_raise() -> None:
    await ConsoleEmailSender().send("x@y.com", "subj", "body")
