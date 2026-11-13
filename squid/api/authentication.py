"""Authenticate callers before guards and meter them after the IP quota."""

from typing import override

from litestar import Request
from litestar.connection import ASGIConnection
from litestar.middleware.authentication import AbstractAuthenticationMiddleware, AuthenticationResult

from squid.api.rate_limit import enforce_route_rate_limits
from squid.api.security import ANONYMOUS, current_caller


class CallerAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    """Expose the authenticated caller through Litestar's request.user and request.auth."""

    @override
    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        # Health and schema routes do not resolve credentials or consume caller quotas.
        path = connection.url.path
        if not (path.startswith("/v1/") or path == "/verify"):
            return AuthenticationResult(user=ANONYMOUS, auth=ANONYMOUS.kind)
        request = Request(connection.scope)
        caller = await current_caller(request)
        if hasattr(request.app.state, "rate_limiter"):
            await enforce_route_rate_limits(request, caller)
        return AuthenticationResult(user=caller, auth=caller.kind)
