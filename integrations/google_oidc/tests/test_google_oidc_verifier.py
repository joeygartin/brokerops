"""Verifier behavior with Google's token check monkeypatched — no network.

We stub `verify_oauth2_token` to return claim dicts (or raise ValueError) and
assert the adapter's audience-trust, email, and allowlist rules around it.
"""

import pytest

from brokerops_core.ports.identity import AuthError
from brokerops_google_oidc.adapter import GoogleOIDCVerifier

CLIENT_ID = "client-123.apps.googleusercontent.com"

Claims = dict[str, object]


def _patch_claims(
    monkeypatch: pytest.MonkeyPatch, claims: Claims | None, *, raises: bool = False
) -> None:
    def fake_verify(token: object, transport: object, audience: object) -> Claims | None:
        if raises:
            raise ValueError("Token expired")
        return claims

    monkeypatch.setattr(
        "brokerops_google_oidc.adapter.google_id_token.verify_oauth2_token", fake_verify
    )


def _valid_claims(**overrides: object) -> Claims:
    base = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "agent@acme.com",
        "email_verified": True,
        "name": "Agent Smith",
        "hd": "acme.com",
    }
    base.update(overrides)
    return base


async def test_valid_token_returns_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims())
    principal = await GoogleOIDCVerifier(CLIENT_ID).verify("tok")
    assert principal.subject == "1234567890"
    assert principal.email == "agent@acme.com"
    assert principal.name == "Agent Smith"


async def test_missing_token_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = GoogleOIDCVerifier(CLIENT_ID)
    with pytest.raises(AuthError) as exc:
        await verifier.verify(None)
    assert exc.value.forbidden is False


async def test_invalid_token_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, None, raises=True)
    with pytest.raises(AuthError) as exc:
        await GoogleOIDCVerifier(CLIENT_ID).verify("tok")
    assert exc.value.forbidden is False


async def test_unexpected_issuer_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims(iss="evil.example"))
    with pytest.raises(AuthError):
        await GoogleOIDCVerifier(CLIENT_ID).verify("tok")


async def test_unverified_email_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims(email_verified=False))
    with pytest.raises(AuthError):
        await GoogleOIDCVerifier(CLIENT_ID).verify("tok")


async def test_disallowed_domain_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims(email="x@other.com", hd="other.com"))
    verifier = GoogleOIDCVerifier(CLIENT_ID, allowed_domain="acme.com")
    with pytest.raises(AuthError) as exc:
        await verifier.verify("tok")
    assert exc.value.forbidden is True


async def test_allowed_domain_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims())
    verifier = GoogleOIDCVerifier(CLIENT_ID, allowed_domain="acme.com")
    assert (await verifier.verify("tok")).email == "agent@acme.com"


async def test_allowed_email_passes_off_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims(email="contractor@gmail.com", hd=None))
    verifier = GoogleOIDCVerifier(CLIENT_ID, allowed_emails=frozenset({"contractor@gmail.com"}))
    assert (await verifier.verify("tok")).email == "contractor@gmail.com"


async def test_email_off_both_lists_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claims(monkeypatch, _valid_claims(email="nope@gmail.com", hd=None))
    verifier = GoogleOIDCVerifier(
        CLIENT_ID, allowed_domain="acme.com", allowed_emails=frozenset({"yes@gmail.com"})
    )
    with pytest.raises(AuthError) as exc:
        await verifier.verify("tok")
    assert exc.value.forbidden is True
