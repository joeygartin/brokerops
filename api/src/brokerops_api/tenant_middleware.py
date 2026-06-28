"""ASGI middleware that binds the deploy's tenant for the duration of a request.

This is the request-boundary half of the tenant-scoping seam (BOP-006): every HTTP
request runs inside ``tenant_scope(deploy_tenant())`` so the data layer reads the
tenant from below the agent, never from a parameter the agent could supply. It is a
*pure ASGI* middleware on purpose — Starlette's ``BaseHTTPMiddleware`` runs the
endpoint in a separate task, which breaks ContextVar propagation; a pure ASGI
middleware binds in the same task as the endpoint and its downstream awaits.

The binding covers operator, webhook, and cron routes uniformly and fails closed: a
request that reaches the data layer always has a tenant bound.
"""

from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from brokerops_core.services.tenancy import tenant_scope


class TenantScopeMiddleware:
    def __init__(self, app: ASGIApp, tenant_provider: Callable[[], str]) -> None:
        self._app = app
        self._tenant_provider = tenant_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # lifespan / websocket: nothing reaches the tenant-scoped data layer.
            await self._app(scope, receive, send)
            return
        with tenant_scope(self._tenant_provider()):
            await self._app(scope, receive, send)
