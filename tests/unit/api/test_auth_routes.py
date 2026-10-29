"""Browser authorization routes over the real provider and session service."""

from collections.abc import Iterator
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from squid.accounts.domain import IdentityProvider
from squid.auth.application.providers import DiscordOAuthProvider
from squid.auth.application.web import WebSessionService
from squid.config import OAuthClientCredentials
from tests.unit.api.fakes import build_app
from tests.unit.auth.application.fakes import AccountServiceRecorder, SessionRepository


@dataclass
class BrowserLogin:
    client: TestClient
    sessions: SessionRepository
    accounts: AccountServiceRecorder
    auth: WebSessionService


@pytest.fixture
def login() -> Iterator[BrowserLogin]:
    def discord(request: httpx.Request) -> httpx.Response:
        payload = {"access_token": "discord-token"} if request.url.path.endswith("/oauth2/token") else {"id": "123"}
        return httpx.Response(200, json=payload)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(discord))
    credentials = OAuthClientCredentials(
        client_id="client",
        client_secret=SecretStr("secret"),
        redirect_uri=AnyHttpUrl("https://test/v1/auth/discord/callback"),
    )
    provider = DiscordOAuthProvider(credentials, upstream)
    sessions = SessionRepository()
    accounts = AccountServiceRecorder()
    auth = WebSessionService(sessions, accounts, {provider.slug: provider}, 336, "test-session-pepper")
    app, database = build_app(web_auth=auth)
    with TestClient(app, base_url="https://test", follow_redirects=False) as client:
        try:
            yield BrowserLogin(client, sessions, accounts, auth)
        finally:
            assert client.portal is not None
            client.portal.call(upstream.aclose)
    assert database.closed


def test_discord_login_consumes_state_and_sets_session_cookies(login: BrowserLogin) -> None:
    authorize = login.client.get("/v1/auth/discord", params={"redirect_to": "/account"})

    assert authorize.status_code == 307
    assert login.sessions.state is not None
    state = login.sessions.state.state
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    assert query["state"] == [state]
    assert query["code_challenge_method"] == ["S256"]

    callback = login.client.get("/v1/auth/discord/callback", params={"code": "c", "state": state})

    assert callback.status_code == 307
    assert callback.headers["location"] == "/account"
    assert login.sessions.state is None
    assert login.accounts.identities == [(IdentityProvider.DISCORD, "123")]
    token = callback.cookies["__Host-squid_session"]
    assert login.sessions.token_hash == login.auth.hash_token(token)
    assert login.sessions.account_id == login.accounts.account_id
    cookies = SimpleCookie()
    for header in callback.headers.get_list("set-cookie"):
        cookies.load(header)
    session_cookie = cookies["__Host-squid_session"]
    assert session_cookie["secure"]
    assert session_cookie["httponly"]
    assert session_cookie["path"] == "/"
    assert session_cookie["samesite"] == "lax"
    assert cookies["squid_csrf"]["secure"]
    assert not cookies["squid_csrf"]["httponly"]

    replay = login.client.get("/v1/auth/discord/callback", params={"code": "c", "state": state})
    assert replay.status_code == 401
    assert login.accounts.identities == [(IdentityProvider.DISCORD, "123")]


def test_provider_route_does_not_swallow_the_csrf_endpoint(login: BrowserLogin) -> None:
    response = login.client.get("/v1/auth/csrf")

    assert response.status_code == 401
    assert login.sessions.state is None


def test_unconfigured_provider_is_not_found(login: BrowserLogin) -> None:
    response = login.client.get("/v1/auth/github")

    assert response.status_code == 404
    assert login.sessions.state is None


@pytest.mark.parametrize("slug", ["Discord", "a" * 33, "-discord", "1discord"])
def test_malformed_slug_cannot_create_authorization_state(login: BrowserLogin, slug: str) -> None:
    response = login.client.get(f"/v1/auth/{slug}")

    assert response.status_code == 422
    assert login.sessions.state is None
