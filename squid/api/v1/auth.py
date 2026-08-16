"""Browser-session routes, templated over the configured identity providers."""

import secrets
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import RedirectResponse, Response

from squid.api.dependencies import CurrentCaller, WebAuth
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.v1.schemas.auth import CsrfTokenResponse
from squid.core.errors import AuthenticationError, ValidationError

router = APIRouter(prefix="/auth", tags=["authentication"])

ProviderSlug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
"""One provider's URL segment. An unknown or unconfigured one is a 404 about the
resource, not a credential failure: "this deployment has no GitHub login" is a fact."""


# `/csrf` and `/logout` are declared before the templated routes on purpose. FastAPI
# matches in declaration order, so `GET /{provider}` would otherwise swallow
# `GET /csrf` -- and swallow it silently, since the request would still succeed, just as
# a 404 for a provider named "csrf". `/logout` is POST and does not actually collide,
# but is hoisted with it for symmetry. `tests/unit/api/test_auth_routes.py` pins this.
@router.get("/csrf", response_model=CsrfTokenResponse, responses=responses(401, 503))
async def csrf_token(request: Request, response: Response, caller: CurrentCaller) -> CsrfTokenResponse:
    """Return the session-bound write token to a credentialed CORS frontend."""
    token = request.cookies.get("squid_csrf")
    if caller.kind != "account" or token is None or not 16 <= len(token) <= 128:
        raise AuthenticationError
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return CsrfTokenResponse(csrf_token=token)


@router.post(
    "/logout",
    status_code=204,
    responses=responses(401, 403, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def logout(request: Request, web_auth: WebAuth, caller: CurrentCaller) -> Response:
    """Revoke the current browser session and clear its cookies."""
    token = request.cookies.get("__Host-squid_session")
    if caller.kind != "account" or token is None or web_auth is None:
        raise AuthenticationError
    await web_auth.logout(token)
    response = Response(status_code=204)
    response.delete_cookie("__Host-squid_session", path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie("squid_csrf", path="/", secure=True, samesite="lax")
    return response


@router.get("/{provider}", response_class=RedirectResponse, responses=responses(400, 404, 503))
async def browser_authorization_start(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    redirect_to: Annotated[str | None, Query(max_length=2_048)] = None,
) -> RedirectResponse:
    """Begin authorization with PKCE and durable one-time state."""
    if web_auth is None:
        raise AuthenticationError
    _validate_redirect(request, redirect_to)
    return RedirectResponse(await web_auth.authorize_url(provider, redirect_to))


@router.get("/{provider}/callback", response_class=RedirectResponse, responses=responses(400, 401, 404, 503))
async def browser_authorization_callback(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    code: Annotated[str, Query(min_length=1, max_length=2_048)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    """Exchange an authorization code and set a revocable opaque session cookie."""
    if web_auth is None:
        raise AuthenticationError
    token, redirect_to = await web_auth.callback(
        provider,
        code,
        state,
        user_agent=request.headers.get("User-Agent"),
    )
    destination = redirect_to or "/"
    _validate_redirect(request, destination)
    response = RedirectResponse(destination)
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
    if redirect_to is None:
        return
    if redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return
    parsed = urlparse(redirect_to)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in request.app.state.config.api.cors_origins:
        msg = "redirect_to must be local or use an allowed CORS origin"
        raise ValidationError(msg, public_context={"field": "redirect_to"})
