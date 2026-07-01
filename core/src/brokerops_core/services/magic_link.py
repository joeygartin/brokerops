"""Magic-link login: issue a single-use emailed token, redeem it for a session.

Pure orchestration over Protocols — the token store, email sender, and session
issuer are all injected, so this logic is unit-tested with in-memory fakes and
carries no framework or I/O of its own. Security properties live here: tokens
are high-entropy, only their SHA-256 hash is persisted, redemption is single-use
and time-bounded, and the allowlist is enforced at both request and redeem time.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from brokerops_core.ports.auth import MagicTokenStore, SessionIssuer, SessionTokens
from brokerops_core.ports.email import EmailSender
from brokerops_core.ports.identity import AuthError, Principal
from brokerops_core.services.identity import EmailAllowlist, RoleResolver

DEFAULT_TTL = timedelta(minutes=15)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MagicLinkService:
    def __init__(
        self,
        store: MagicTokenStore,
        email: EmailSender,
        session_issuer: SessionIssuer,
        allowlist: EmailAllowlist,
        public_base_url: str,
        roles: RoleResolver | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._store = store
        self._email = email
        self._issuer = session_issuer
        self._allowlist = allowlist
        self._roles = roles or RoleResolver()
        self._base_url = public_base_url.rstrip("/")
        self._ttl = ttl

    async def request(self, email: str) -> None:
        """Mint + email a sign-in link. A no-op for non-allowlisted addresses so
        callers can always return 202 without leaking who is permitted."""
        email = email.strip().lower()
        if not email or not self._allowlist.permits(email):
            return
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + self._ttl
        await self._store.create(_hash(token), email, expires_at)
        link = f"{self._base_url}/auth/callback?token={token}"
        text = (
            "Sign in to brokerops by opening this link (valid for "
            f"{int(self._ttl.total_seconds() // 60)} minutes):\n\n{link}\n\n"
            "If you didn't request this, you can ignore this email."
        )
        await self._email.send(email, "Your brokerops sign-in link", text)

    async def redeem(self, token: str) -> SessionTokens:
        """Validate a token and return the session credential pair. Raises
        AuthError on a missing/used/expired token (401) or a now-disallowed email
        (403)."""
        record = await self._store.consume(_hash(token))
        if record is None:
            raise AuthError("invalid or already-used sign-in link")
        if record.expires_at < datetime.now(UTC):
            raise AuthError("sign-in link expired")
        if not self._allowlist.permits(record.email):
            raise AuthError(f"{record.email} is not permitted", forbidden=True)
        principal = Principal(
            subject=record.email,
            email=record.email,
            name="",
            verified=True,
            role=self._roles.role_for(record.email),
        )
        return SessionTokens(
            access=self._issuer.issue(principal),
            refresh=self._issuer.issue_refresh(principal),
        )
