"""Discord OAuth2 browser-session routes."""

import secrets
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response

from squid.api.dependencies import CurrentPrincipal, WebAuth
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.core.errors import AuthenticationError, ValidationError

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/discord", response_class=RedirectResponse, responses=responses(400, 503))
async def discord_authorize(
    request: Request,
    web_auth: WebAuth,
    redirect_to: Annotated[str | None, Query(max_length=2_048)] = None,
) -> RedirectResponse:
    """Begin Discord authorization with PKCE and durable one-time state."""
    if web_auth is None:
        raise AuthenticationError
    _validate_redirect(request, redirect_to)
    return RedirectResponse(await web_auth.authorize_url(redirect_to))


@router.get("/discord/callback", response_class=RedirectResponse, responses=responses(400, 401, 503))
async def discord_callback(
    request: Request,
    web_auth: WebAuth,
    code: Annotated[str, Query(min_length=1, max_length=2_048)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    """Exchange a Discord code and set a revocable opaque session cookie."""
    if web_auth is None:
        raise AuthenticationError
    token, redirect_to = await web_auth.callback(
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


@router.post(
    "/logout",
    status_code=204,
    responses=responses(401, 403, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def logout(request: Request, web_auth: WebAuth, principal: CurrentPrincipal) -> Response:
    """Revoke the current browser session and clear its cookies."""
    token = request.cookies.get("__Host-squid_session")
    if principal.kind != "user" or token is None or web_auth is None:
        raise AuthenticationError
    await web_auth.logout(token)
    response = Response(status_code=204)
    response.delete_cookie("__Host-squid_session", path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie("squid_csrf", path="/", secure=True, samesite="lax")
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
