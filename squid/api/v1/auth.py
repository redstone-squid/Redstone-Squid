"""Browser-session routes, templated over the configured identity providers."""

import secrets
from typing import Annotated
from urllib.parse import urlparse

from litestar import Request, Response, Router, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import PathParameter, QueryParameter, SkipValidation
from litestar.response import Redirect

from squid.api.contract import ANONYMOUS, WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import CurrentCaller, WebAuth
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.v1.schemas.auth import CsrfTokenResponse
from squid.core.errors import AuthenticationError, ValidationError

ProviderSlug = Annotated[str, PathParameter(pattern="^[a-z][a-z0-9_-]{0,31}$")]
"""One provider's URL segment. An unknown or unconfigured one is a 404 about the
resource, not a credential failure: "this deployment has no GitHub login" is a fact.
"""


@get(
    "/csrf",
    responses=responses(401, 503),
    operation_id="browser_csrf_get",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
)
async def csrf_token(request: Request, caller: CurrentCaller) -> Response[CsrfTokenResponse]:
    """Return the session-bound write token to a credentialed CORS frontend."""
    response_headers: dict[str, str] = {}
    token = request.cookies.get("squid_csrf")
    if caller.kind != "account" or token is None or (not 16 <= len(token) <= 128):
        raise AuthenticationError
    response_headers["Cache-Control"] = "no-store"
    response_headers["Pragma"] = "no-cache"
    return Response(content=CsrfTokenResponse(csrf_token=token), headers=response_headers, status_code=200)


@post(
    "/logout",
    status_code=204,
    responses=responses(401, 403, 409, 503),
    operation_id="browser_session_revoke",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def logout(
    request: Request, web_auth: WebAuth, caller: CurrentCaller, *, _reservation: NamedDependency[SkipValidation[None]]
) -> Response[None]:
    """Revoke the current browser session and clear its cookies."""
    token = request.cookies.get("__Host-squid_session")
    if caller.kind != "account" or token is None or web_auth is None:
        raise AuthenticationError
    await web_auth.logout(token)
    response = Response(content=None, status_code=204)
    response.set_cookie("__Host-squid_session", "", max_age=0, path="/", secure=True, httponly=True, samesite="lax")
    response.set_cookie("squid_csrf", "", max_age=0, path="/", secure=True, samesite="lax")
    return response


@get(
    "/{provider:str}",
    responses=responses(400, 404, 422, 503),
    operation_id="browser_authorization_start",
    opt=contract(security=[ANONYMOUS], cli=browser_only()),
    status_code=307,
)
async def browser_authorization_start(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    redirect_to: Annotated[str | None, QueryParameter(max_length=2048)] = None,
) -> Redirect:
    """Begin authorization with PKCE and durable one-time state."""
    if web_auth is None:
        raise AuthenticationError
    _validate_redirect(request, redirect_to)
    return Redirect(await web_auth.authorize_url(provider, redirect_to), status_code=307)


@get(
    "/{provider:str}/callback",
    responses=responses(400, 401, 404, 422, 503),
    operation_id="browser_authorization_callback",
    opt=contract(security=[ANONYMOUS], cli=browser_only()),
    status_code=307,
)
async def browser_authorization_callback(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    code: Annotated[str, QueryParameter(min_length=1, max_length=2048)],
    oauth_state: Annotated[str, QueryParameter(name="state", min_length=1, max_length=512)],
) -> Redirect:
    """Exchange an authorization code and set a revocable opaque session cookie."""
    if web_auth is None:
        raise AuthenticationError
    token, redirect_to = await web_auth.callback(
        provider, code, oauth_state, user_agent=request.headers.get("User-Agent")
    )
    destination = redirect_to or "/"
    _validate_redirect(request, destination)
    response = Redirect(destination, status_code=307)
    response.set_cookie(
        "__Host-squid_session",
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=request.app.state.config.oauth.session_ttl_hours * 3600,
    )
    response.set_cookie("squid_csrf", secrets.token_urlsafe(24), secure=True, samesite="lax", path="/")
    return response


def _validate_redirect(request: Request, redirect_to: str | None) -> None:
    """Reject redirects outside local paths and configured CORS origins."""
    if redirect_to is None:
        return
    if redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return
    parsed = urlparse(redirect_to)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in request.app.state.config.api.cors_origins:
        msg = "redirect_to must be local or use an allowed CORS origin"
        raise ValidationError(msg, public_context={"field": "redirect_to"})


router = Router(
    path="/auth",
    tags=["authentication"],
    route_handlers=[csrf_token, logout, browser_authorization_start, browser_authorization_callback],
)
