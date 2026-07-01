from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from brokerops_core.ports.identity import Principal


class ConsumedToken(BaseModel):
    """A magic-login token row returned by an atomic consume."""

    email: str
    expires_at: datetime


class MagicTokenStore(Protocol):
    """Persistence for single-use magic-login tokens.

    Only the SHA-256 hash of a token is ever stored. `consume` must be atomic —
    it marks the row used and returns it only if it was previously unused — so a
    token cannot be redeemed twice even under a race. core/ depends only on this
    Protocol; the SQL/in-memory implementations live in the api layer.
    """

    async def create(self, token_hash: str, email: str, expires_at: datetime) -> None: ...

    async def consume(self, token_hash: str) -> ConsumedToken | None: ...


class SessionTokens(BaseModel):
    """The credential pair a client receives at login.

    ``access`` is the short-lived bearer sent on every request; ``refresh`` is a
    longer-lived token the client exchanges (without re-login) for a fresh access
    token once the access token expires. Both are opaque to core — the api layer
    mints them as signed JWTs.
    """

    model_config = ConfigDict(extra="forbid")

    access: str
    refresh: str


class SessionIssuer(Protocol):
    """Mints the bearer credentials a client carries after login.

    Magic-link redemption authenticates once, then hands back an access token the
    browser sends on every subsequent request plus a refresh token it exchanges
    for a new access token on expiry. The concrete issuer (signed JWTs) lives in
    the api layer; core only needs to call it.
    """

    def issue(self, principal: Principal) -> str: ...

    def issue_refresh(self, principal: Principal) -> str: ...
