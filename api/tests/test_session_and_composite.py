"""Session JWT round-trip + the composite verifier's try-each behavior."""

from datetime import timedelta

import pytest

from brokerops_api.auth.composite import CompositeIdentityVerifier
from brokerops_api.auth.session import SessionTokenService, SessionTokenVerifier
from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal

KEY = "test-signing-key"


def _principal() -> Principal:
    return Principal(subject="op@acme.com", email="op@acme.com", name="Op")


async def test_session_round_trip() -> None:
    token = SessionTokenService(KEY).issue(_principal())
    principal = await SessionTokenVerifier(KEY).verify(token)
    assert principal.email == "op@acme.com"


async def test_session_rejects_wrong_key() -> None:
    token = SessionTokenService(KEY).issue(_principal())
    with pytest.raises(AuthError):
        await SessionTokenVerifier("other-key").verify(token)


async def test_session_rejects_expired() -> None:
    token = SessionTokenService(KEY, ttl=timedelta(seconds=-1)).issue(_principal())
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(token)


async def test_session_rejects_missing() -> None:
    with pytest.raises(AuthError):
        await SessionTokenVerifier(KEY).verify(None)


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
