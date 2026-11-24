"""Authenticate callers before guards and meter them after the IP quota."""

from typing import override

from litestar import Request
from litestar.connection import ASGIConnection
from litestar.middleware.authentication import AbstractAuthenticationMiddleware, AuthenticationResult
from litestar.types import Receive, Scope, Send

from squid.api.rate_limit import enforce_route_rate_limits
from squid.api.security import ANONYMOUS, authenticated_caller, current_caller
from squid.core.actors import impersonation_use


class CallerAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    """Expose the authenticated caller through Litestar's request.user and request.auth."""

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with impersonation_use.set(None):
            await super().__call__(scope, receive, send)

    @override
    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        # Health and schema routes do not resolve credentials or consume caller quotas.
        path = connection.url.path
        if not (path.startswith("/v1/") or path == "/verify"):
            return AuthenticationResult(user=ANONYMOUS, auth=ANONYMOUS.kind)
        request = Request(connection.scope)
        if hasattr(request.app.state, "rate_limiter"):
            await enforce_route_rate_limits(request, await authenticated_caller(request))
        caller = await current_caller(request)
        if caller.acting_context is not None:
            impersonation_use.set(caller.acting_context.effective.audit_use_id)
        return AuthenticationResult(user=caller, auth=caller.kind)
