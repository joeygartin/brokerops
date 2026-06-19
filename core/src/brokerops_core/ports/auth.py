from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

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


class SessionIssuer(Protocol):
    """Mints the bearer credential a client carries after login.

    Magic-link redemption authenticates once, then hands back a session token
    the browser sends on every subsequent request. The concrete issuer (a signed
    JWT) lives in the api layer; core only needs to call it.
    """

    def issue(self, principal: Principal) -> str: ...
