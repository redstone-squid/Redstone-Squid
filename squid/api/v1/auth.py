"""Browser-session routes, templated over the configured identity providers."""

import secrets
from typing import Annotated
from urllib.parse import urlencode, urlparse

from litestar import Request, Response, Router, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import PathParameter, QueryParameter, SkipValidation
from litestar.response import Redirect
from pydantic import BaseModel

from squid.api.contract import ANONYMOUS, WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import CurrentCaller, WebAuth, get_web_auth
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.private_responses import private_response
from squid.api.security import requires
from squid.api.v1.schemas.auth import CsrfTokenResponse
from squid.core.auth.application.web import CallbackFailure
from squid.core.errors import AuthenticationError, ValidationError
from squid.core.permissions.domain.catalogue import ACCOUNT_SELF_MANAGE
from squid.core.validation import (
    FieldErrorCode,
    SimpleIssue,
)

ProviderSlug = Annotated[str, PathParameter(pattern="^[a-z][a-z0-9_-]{0,31}$")]
"""One provider's URL segment. An unknown or unconfigured one is a 404 about the
resource, not a credential failure: "this deployment has no GitHub login" is a fact.
"""


class BrowserProvider(BaseModel):
    id: str


@get("/providers", operation_id="browser_providers_list", security=[ANONYMOUS], opt=contract(cli=browser_only()))
async def providers(web_auth: WebAuth) -> list[BrowserProvider]:
    """List enabled browser sign-in providers in display order."""
    return [BrowserProvider(id=slug) for slug in web_auth.provider_ids] if web_auth else []


@get(
    "/csrf",
    responses=responses(401, 503),
    operation_id="browser_csrf_get",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
)
@private_response
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
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def logout(
    request: Request, web_auth: WebAuth, caller: CurrentCaller, *, _reservation: NamedDependency[SkipValidation[None]]
) -> Response[None]:
    """Revoke the current browser session and clear its cookies."""
    token = request.cookies.get("__Host-squid_session")
    if caller.kind != "account" or token is None or web_auth is None:
        raise AuthenticationError
    await web_auth.logout(token, context=caller.context())
    response = Response(content=None, status_code=204)
    response.set_cookie("__Host-squid_session", "", max_age=0, path="/", secure=True, httponly=True, samesite="lax")
    response.set_cookie("squid_csrf", "", max_age=0, path="/", secure=True, samesite="lax")
    return response


@get(
    "/{provider:str}",
    responses=responses(400, 404, 422, 503),
    operation_id="browser_authorization_start",
    security=[ANONYMOUS],
    opt=contract(cli=browser_only()),
    status_code=307,
)
async def browser_authorization_start(
    caller: CurrentCaller,
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    redirect_to: Annotated[str | None, QueryParameter(max_length=2048)] = None,
) -> Redirect:
    """Begin authorization with PKCE and durable one-time state."""
    if web_auth is None:
        raise AuthenticationError
    _validate_redirect(request, redirect_to)
    return Redirect(await web_auth.authorize_url(provider, redirect_to, context=caller.context()), status_code=307)


class LinkAuthorization(BaseModel):
    authorization_url: str


@post(
    "/{provider:str}/link",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_MANAGE)],
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
    operation_id="browser_provider_link",
    status_code=200,
)
async def link_provider(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    caller: CurrentCaller,
    _reservation: NamedDependency[SkipValidation[None]],
    redirect_to: Annotated[str | None, QueryParameter(max_length=2048)] = None,
) -> LinkAuthorization:
    """Start identity linking for the consenting browser account."""
    if web_auth is None or caller.kind != "account":
        raise AuthenticationError
    caller.require_consented_account()
    _validate_redirect(request, redirect_to)
    url = await web_auth.authorize_url(provider, redirect_to, link=True, context=caller.context())
    return LinkAuthorization(authorization_url=url)


@get(
    "/{provider:str}/callback",
    responses=responses(400, 401, 404, 422, 503),
    operation_id="browser_authorization_callback",
    security=[ANONYMOUS],
    opt=contract(cli=browser_only()),
    status_code=307,
)
async def browser_authorization_callback(
    request: Request,
    web_auth: WebAuth,
    provider: ProviderSlug,
    caller: CurrentCaller,
    code: Annotated[str | None, QueryParameter(max_length=2048)] = None,
    oauth_state: Annotated[str | None, QueryParameter(name="state", max_length=512)] = None,
    error: Annotated[str | None, QueryParameter(max_length=256)] = None,
) -> Redirect:
    """Exchange an authorization code and set a revocable opaque session cookie."""
    public_site = request.app.state.config.notification.public_site_url
    sign_in = f"{str(public_site).rstrip('/')}/sign-in" if public_site else "/sign-in"
    if not oauth_state or web_auth is None or provider not in web_auth.provider_ids:
        return Redirect(f"{sign_in}?auth_error=invalid_state", status_code=307)
    try:
        token, redirect_to = await web_auth.callback(
            provider,
            code,
            oauth_state,
            user_agent=request.headers.get("User-Agent"),
            error=error,
            context=caller.context(),
            consent_current=caller.kind == "account" and not caller.consent_pending,
        )
    except CallbackFailure as failure:
        params = {"auth_error": failure.callback_code}
        if failure.redirect_to:
            _validate_redirect(request, failure.redirect_to)
            params["redirect_to"] = failure.redirect_to
        return Redirect(f"{sign_in}?{urlencode(params)}", status_code=307)
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
    if "\\" in redirect_to or any(ord(char) < 32 for char in redirect_to):
        msg = "redirect_to contains invalid URL characters"
        raise ValidationError(msg, issues=(SimpleIssue(field="/query/redirect_to", code=FieldErrorCode.INVALID_VALUE),))
    if redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return
    parsed = urlparse(redirect_to)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in request.app.state.config.api.cors_origins:
        msg = "redirect_to must be local or use an allowed CORS origin"
        raise ValidationError(msg, issues=(SimpleIssue(field="/query/redirect_to", code=FieldErrorCode.INVALID_VALUE),))


router = Router(
    dependencies={"web_auth": Provide(get_web_auth, sync_to_thread=False)},
    path="/auth",
    tags=["authentication"],
    route_handlers=[
        providers,
        link_provider,
        csrf_token,
        logout,
        browser_authorization_start,
        browser_authorization_callback,
    ],
)
