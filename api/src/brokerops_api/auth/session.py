"""Session tokens — the bearers a client carries after any login method.

Magic-link redemption (and any future non-OIDC method) authenticates once, then
hands the browser a short-lived signed **access** JWT it sends on every request
plus a longer-lived **refresh** JWT it exchanges for a new access token on expiry
(ADR-0013). The access token is verified per-request by SessionTokenVerifier,
which implements the core IdentityVerifier Protocol — so a session access JWT and
a Google ID token are interchangeable bearers behind one `verify()` (ADR-0008).

HS256 with a shared signing key: there is a single trusted issuer (this api), so
symmetric signing is sufficient and keyless verification isn't needed.

Refresh tokens are **stateless** (no server-side store) and carry an absolute TTL
that `/auth/refresh` never extends, so a leaked refresh token grants at most
REFRESH_TTL of access and cannot be renewed indefinitely (fail-closed). A `typ`
claim keeps the two kinds distinct: a refresh token is rejected as a request
bearer, and only a refresh token is accepted at the refresh endpoint.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import jwt

from brokerops_core.ports.auth import SessionTokens
from brokerops_core.ports.identity import AuthError, Principal, Role
from brokerops_core.services.identity import EmailAllowlist, RoleResolver

ISSUER = "brokerops"
# Short access token so a stolen bearer expires quickly; the refresh token below
# keeps the operator signed in without re-login (ADR-0013).
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(hours=24)
ACCESS_TYP = "access"
REFRESH_TYP = "refresh"


class SessionTokenService:
    """Issues session JWTs — access and refresh (implements core's SessionIssuer)."""

    def __init__(
        self,
        signing_key: str,
        access_ttl: timedelta = ACCESS_TTL,
        refresh_ttl: timedelta = REFRESH_TTL,
    ) -> None:
        self._key = signing_key
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def _encode(
        self,
        principal: Principal,
        typ: str,
        ttl: timedelta,
        not_after: datetime | None = None,
    ) -> str:
        now = datetime.now(UTC)
        exp = now + ttl
        # Never outlive an upstream deadline (the refresh token's own expiry): the
        # absolute session length must stay bounded by the refresh TTL, not extend a
        # full access lifetime past it (ADR-0013).
        if not_after is not None and not_after < exp:
            exp = not_after
        payload = {
            "iss": ISSUER,
            "typ": typ,
            "sub": principal.subject,
            "email": principal.email,
            "name": principal.name,
            "role": principal.role.value,
            "iat": now,
            "exp": exp,
        }
        return jwt.encode(payload, self._key, algorithm="HS256")

    def issue(self, principal: Principal, not_after: datetime | None = None) -> str:
        return self._encode(principal, ACCESS_TYP, self._access_ttl, not_after)

    def issue_refresh(self, principal: Principal) -> str:
        return self._encode(principal, REFRESH_TYP, self._refresh_ttl)


def _principal_from_claims(claims: dict[str, object]) -> Principal:
    # A token issued before RBAC (or with a stale/unknown value) has no usable role
    # claim; default to VIEWER (least privilege) so a missing claim grants read-only
    # access, never a write-capable role. A live login always stamps a real role, so
    # only stale pre-RBAC tokens hit this — they re-prompt to upgrade.
    raw_role = claims.get("role")
    try:
        role = Role(raw_role) if raw_role else Role.VIEWER
    except ValueError:
        role = Role.VIEWER
    return Principal(
        subject=str(claims["sub"]),
        email=str(claims.get("email", "")),
        name=str(claims.get("name", "")),
        verified=True,
        role=role,
    )


def _decode(token: str, key: str) -> dict[str, object]:
    return jwt.decode(
        token,
        key,
        algorithms=["HS256"],
        issuer=ISSUER,
        options={"require": ["exp", "iss", "sub"]},
    )


class SessionTokenVerifier:
    """Verifies session **access** JWTs (implements core's IdentityVerifier).

    Only an access token authorizes a request. The `typ` allow-list is closed
    (fail-closed): the bearer must carry `typ == "access"` exactly — a refresh
    token, any other/unknown `typ`, or a token with no `typ` at all (e.g. a
    pre-split token) is rejected. Pre-split tokens simply re-login once on deploy;
    there is no accepted no-`typ` bearer.
    """

    def __init__(self, signing_key: str) -> None:
        self._key = signing_key

    async def verify(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("missing bearer token")
        try:
            claims = _decode(token, self._key)
        except jwt.PyJWTError as exc:  # bad signature, expiry, issuer, or shape
            raise AuthError(f"invalid session token: {exc}") from exc
        if claims.get("typ") != ACCESS_TYP:
            raise AuthError(f"token type {claims.get('typ')!r} cannot be used as a bearer")
        return _principal_from_claims(claims)


class SessionRefresher:
    """Exchanges a valid refresh token for a fresh access token (ADR-0013).

    Re-checks the allowlist and re-resolves the role from the email on every
    refresh, so a de-allowlisted or demoted operator loses (or has downgraded)
    access within one access-token lifetime. The refresh token's own TTL is
    **not** extended, and the new access token cannot outlive it — the absolute
    session length is bounded by REFRESH_TTL (a refresh near expiry yields a
    correspondingly short access token, then re-login).
    """

    def __init__(
        self,
        signing_key: str,
        allowlist: EmailAllowlist,
        roles: RoleResolver,
        service: SessionTokenService,
    ) -> None:
        self._key = signing_key
        self._allowlist = allowlist
        self._roles = roles
        self._service = service

    def refresh(self, refresh_token: str) -> str:
        try:
            claims = _decode(refresh_token, self._key)
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid refresh token: {exc}") from exc
        if claims.get("typ") != REFRESH_TYP:
            raise AuthError("not a refresh token")
        email = str(claims.get("email", ""))
        if not email:
            raise AuthError("refresh token missing email")
        if not self._allowlist.permits(email):
            raise AuthError(f"{email} is not permitted", forbidden=True)
        principal = Principal(
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name", "")),
            verified=True,
            role=self._roles.role_for(email),
        )
        # Clamp the new access token at the refresh token's own expiry so refreshing
        # can never push access past the 24h absolute cap (ADR-0013).
        refresh_exp = datetime.fromtimestamp(cast(float, claims["exp"]), UTC)
        return self._service.issue(principal, not_after=refresh_exp)


__all__ = [
    "ISSUER",
    "SessionRefresher",
    "SessionTokenService",
    "SessionTokenVerifier",
    "SessionTokens",
]
