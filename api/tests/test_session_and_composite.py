"""Session JWT round-trip + the composite verifier's try-each behavior."""

from datetime import timedelta

import pytest

from brokerops_api.auth.composite import CompositeIdentityVerifier
from brokerops_api.auth.session import (
    SessionRefresher,
    SessionTokenService,
    SessionTokenVerifier,
)
from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal, Role
from brokerops_core.services.identity import EmailAllowlist, RoleResolver

KEY = "test-signing-key"


def _principal() -> Principal:
    return Principal(subject="op@acme.com", email="op@acme.com", name="Op")


async def test_session_round_trip() -> None:
    token = SessionTokenService(KEY).issue(_principal())
    principal = await SessionTokenVerifier(KEY).verify(token)
    assert principal.email == "op@acme.com"


async def test_session_round_trips_role() -> None:
    admin = Principal(subject="a@x.com", email="a@x.com", role=Role.ADMIN)
    token = SessionTokenService(KEY).issue(admin)
    assert (await SessionTokenVerifier(KEY).verify(token)).role is Role.ADMIN


async def test_session_defaults_missing_role_to_viewer() -> None:
    # An access token with no role claim resolves to the lowest privilege (read-only),
    # never a write-capable role — least privilege when the claim is absent.
    import jwt

    from brokerops_api.auth.session import ACCESS_TYP, ISSUER

    roleless = jwt.encode(
        {"iss": ISSUER, "typ": ACCESS_TYP, "sub": "a@x.com", "email": "a@x.com", "exp": 9999999999},
        KEY,
        algorithm="HS256",
    )
    assert (await SessionTokenVerifier(KEY).verify(roleless)).role is Role.VIEWER


async def test_session_rejects_wrong_key() -> None:
    token = SessionTokenService(KEY).issue(_principal())
    with pytest.raises(AuthError):
        await SessionTokenVerifier("other-key").verify(token)


async def test_session_rejects_expired() -> None:
    token = SessionTokenService(KEY, access_ttl=timedelta(seconds=-1)).issue(_principal())
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(token)


async def test_session_rejects_missing() -> None:
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(None)


# ── refresh tokens ─────────────────────────────────────────────────────


async def test_refresh_token_rejected_as_bearer() -> None:
    # A refresh token must never authorize a protected route: it is a renewal
    # credential, not an access credential (ADR-0013).
    refresh = SessionTokenService(KEY).issue_refresh(_principal())
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(refresh)


async def test_verifier_rejects_unknown_typ() -> None:
    # The typ allow-list is closed: only access (or a legacy no-typ token) is a
    # bearer. Any other/unknown typ is rejected fail-closed, never silently admitted.
    import jwt

    from brokerops_api.auth.session import ISSUER

    weird = jwt.encode(
        {"iss": ISSUER, "typ": "banana", "sub": "a@x.com", "exp": 9999999999},
        KEY,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(weird)


def _refresher(allowlist: EmailAllowlist, roles: RoleResolver | None = None) -> SessionRefresher:
    return SessionRefresher(
        signing_key=KEY,
        allowlist=allowlist,
        roles=roles or RoleResolver(),
        service=SessionTokenService(KEY),
    )


async def test_refresh_yields_a_usable_access_token() -> None:
    refresh = SessionTokenService(KEY).issue_refresh(_principal())
    access = _refresher(EmailAllowlist()).refresh(refresh)
    principal = await SessionTokenVerifier(KEY).verify(access)
    assert principal.email == "op@acme.com"


async def test_refresh_rejects_an_access_token() -> None:
    # The endpoint only accepts refresh-typ tokens; an access token cannot be
    # replayed there to mint fresh access.
    access = SessionTokenService(KEY).issue(_principal())
    with pytest.raises(AuthError):
        _refresher(EmailAllowlist()).refresh(access)


async def test_refresh_reauthorizes_against_the_allowlist() -> None:
    # An operator dropped from the allowlist after login cannot refresh (403).
    refresh = SessionTokenService(KEY).issue_refresh(_principal())
    tight = _refresher(EmailAllowlist(allowed_emails=frozenset({"someone@else.com"})))
    with pytest.raises(AuthError) as exc:
        tight.refresh(refresh)
    assert exc.value.forbidden is True


async def test_refresh_re_resolves_role() -> None:
    # Role is resolved fresh on refresh, so a promotion takes effect within one
    # access-token lifetime rather than requiring re-login.
    refresh = SessionTokenService(KEY).issue_refresh(_principal())
    roles = RoleResolver(admin_emails=frozenset({"op@acme.com"}))
    access = _refresher(EmailAllowlist(), roles).refresh(refresh)
    assert (await SessionTokenVerifier(KEY).verify(access)).role is Role.ADMIN


async def test_refresh_rejects_expired() -> None:
    stale = SessionTokenService(KEY, refresh_ttl=timedelta(seconds=-1)).issue_refresh(_principal())
    with pytest.raises(AuthError):
        _refresher(EmailAllowlist()).refresh(stale)


async def test_refresh_clamps_access_to_refresh_expiry() -> None:
    # A refresh token near its own expiry must not mint a full-length (1h) access
    # token that outlives it — the new access exp is clamped at the refresh exp so
    # the 24h absolute cap actually holds (ADR-0013).
    import jwt

    from brokerops_api.auth.session import ISSUER

    near_expiry = SessionTokenService(
        KEY, access_ttl=timedelta(hours=1), refresh_ttl=timedelta(seconds=30)
    ).issue_refresh(_principal())
    # The refresher's own service issues 1h access tokens by default.
    access = _refresher(EmailAllowlist()).refresh(near_expiry)
    refresh_exp = jwt.decode(
        near_expiry, KEY, algorithms=["HS256"], issuer=ISSUER, options={"verify_exp": False}
    )["exp"]
    access_exp = jwt.decode(access, KEY, algorithms=["HS256"], issuer=ISSUER)["exp"]
    # Clamped to the refresh expiry, well short of a full 1h access lifetime.
    assert access_exp == refresh_exp


class _Yes:
    def __init__(self, email: str) -> None:
        self._email = email

    async def verify(self, token: str | None) -> Principal:
        return Principal(subject=self._email, email=self._email)


class _No:
    def __init__(self, *, forbidden: bool = False) -> None:
        self._forbidden = forbidden

    async def verify(self, token: str | None) -> Principal:
        raise AuthError("nope", forbidden=self._forbidden)


async def test_composite_returns_first_success() -> None:
    verifiers: list[IdentityVerifier] = [_No(), _Yes("win@x.com")]
    principal = await CompositeIdentityVerifier(verifiers).verify("tok")
    assert principal.email == "win@x.com"


async def test_composite_all_fail_is_401_unless_forbidden() -> None:
    plain = CompositeIdentityVerifier([_No(), _No()])
    with pytest.raises(AuthError) as exc:
        await plain.verify("tok")
    assert exc.value.forbidden is False

    with_forbidden = CompositeIdentityVerifier([_No(), _No(forbidden=True)])
    with pytest.raises(AuthError) as exc2:
        await with_forbidden.verify("tok")
    assert exc2.value.forbidden is True


def test_composite_requires_a_verifier() -> None:
    with pytest.raises(ValueError):
        CompositeIdentityVerifier([])
