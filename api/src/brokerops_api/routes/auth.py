"""Auth bootstrap, magic-link login, and identity echo.

`/auth/config` is public: the SPA reads it at startup to learn which login
methods are enabled (and the Google client id) so it can render the right UI.
The OAuth client id is not a secret, so serving it at runtime keeps one frontend
image per release (ADR-0003). Magic-link request/redeem implement the email
login flow (ADR-0008); `/auth/me` echoes the authenticated principal.
"""

import os
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brokerops_api.deps import (
    PrincipalDep,
    configured_auth_methods,
    get_magic_link_service,
)
from brokerops_core.ports.identity import AuthError
from brokerops_core.services.magic_link import MagicLinkService

router = APIRouter(prefix="/auth", tags=["auth"])

MagicServiceDep = Annotated[MagicLinkService, Depends(get_magic_link_service)]

# Coarse in-memory throttle on link requests, per email — enough to blunt abuse
# of a single instance; a shared limiter is a future upgrade (ADR-0008).
_RATE_WINDOW_SECONDS = 60.0
_RATE_MAX = 3
_recent_requests: dict[str, list[float]] = defaultdict(list)


class MagicRequest(BaseModel):
    email: str


class MagicRedeem(BaseModel):
    token: str


class SessionResponse(BaseModel):
    # The SPA stores this bearer, then calls /auth/me for the principal.
    session_token: str


@router.get("/config")
async def auth_config() -> dict[str, object]:
    methods = configured_auth_methods()
    return {
        "enabled": bool(methods),
        "methods": methods,
        "client_id": os.environ.get("GOOGLE_OIDC_CLIENT_ID") if "google" in methods else None,
    }


@router.post("/magic/request", status_code=202)
async def request_magic_link(body: MagicRequest, service: MagicServiceDep) -> dict[str, str]:
    # Always 202 regardless of allowlist membership — never reveal who is
    # permitted. The service silently no-ops for non-allowlisted addresses.
    email = body.email.lower()
    now = time.monotonic()
    hits = [t for t in _recent_requests[email] if now - t < _RATE_WINDOW_SECONDS]
    if len(hits) >= _RATE_MAX:
        raise HTTPException(status_code=429, detail="too many requests; try again shortly")
    hits.append(now)
    _recent_requests[email] = hits
    await service.request(email)
    return {"status": "accepted"}


@router.post("/magic/redeem")
async def redeem_magic_link(body: MagicRedeem, service: MagicServiceDep) -> SessionResponse:
    try:
        session_token = await service.redeem(body.token)
    except AuthError as exc:
        raise HTTPException(status_code=403 if exc.forbidden else 401, detail=str(exc)) from exc
    return SessionResponse(session_token=session_token)


@router.get("/me")
async def auth_me(principal: PrincipalDep) -> dict[str, object]:
    return principal.model_dump()
