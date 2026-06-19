"""Auth bootstrap + identity echo.

`/auth/config` is public: the SPA reads it at startup to decide whether to show
a Google sign-in (and with which client id) or run as the demo operator. The
OAuth client id is not a secret (it's embedded in the browser flow), so serving
it at runtime keeps one frontend image per release (ADR-0003) — nothing is
baked per client. `/auth/me` echoes the authenticated principal.
"""

import os
from typing import Annotated

from fastapi import APIRouter, Depends

from brokerops_api.deps import PrincipalDep, get_identity_verifier
from brokerops_core.ports.identity import IdentityVerifier
from brokerops_core.services.identity import DemoIdentityVerifier

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
async def auth_config(
    verifier: Annotated[IdentityVerifier, Depends(get_identity_verifier)],
) -> dict[str, object]:
    enabled = not isinstance(verifier, DemoIdentityVerifier)
    return {
        "enabled": enabled,
        "client_id": os.environ.get("GOOGLE_OIDC_CLIENT_ID") if enabled else None,
    }


@router.get("/me")
async def auth_me(principal: PrincipalDep) -> dict[str, object]:
    return principal.model_dump()
