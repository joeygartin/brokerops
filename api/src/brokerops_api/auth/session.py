"""Session tokens — the bearer a client carries after any login method.

Magic-link redemption (and any future non-OIDC method) authenticates once, then
hands the browser a short-lived signed JWT it sends on every request. The same
token is verified per-request by SessionTokenVerifier, which implements the core
IdentityVerifier Protocol — so a session JWT and a Google ID token are
interchangeable bearers behind one `verify()` (ADR-0008).

HS256 with a shared signing key: there is a single trusted issuer (this api), so
symmetric signing is sufficient and keyless verification isn't needed.
"""

from datetime import UTC, datetime, timedelta

import jwt

from brokerops_core.ports.identity import AuthError, Principal

ISSUER = "brokerops"
DEFAULT_TTL = timedelta(hours=8)


class SessionTokenService:
    """Issues session JWTs (implements core's SessionIssuer)."""

    def __init__(self, signing_key: str, ttl: timedelta = DEFAULT_TTL) -> None:
        self._key = signing_key
        self._ttl = ttl

    def issue(self, principal: Principal) -> str:
        now = datetime.now(UTC)
        payload = {
            "iss": ISSUER,
            "sub": principal.subject,
            "email": principal.email,
            "name": principal.name,
            "iat": now,
            "exp": now + self._ttl,
        }
        return jwt.encode(payload, self._key, algorithm="HS256")


class SessionTokenVerifier:
    """Verifies session JWTs (implements core's IdentityVerifier)."""

    def __init__(self, signing_key: str) -> None:
        self._key = signing_key

    async def verify(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("missing bearer token")
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                issuer=ISSUER,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:  # bad signature, expiry, issuer, or shape
            raise AuthError(f"invalid session token: {exc}") from exc
        return Principal(
            subject=str(claims["sub"]),
            email=str(claims.get("email", "")),
            name=str(claims.get("name", "")),
            verified=True,
        )
