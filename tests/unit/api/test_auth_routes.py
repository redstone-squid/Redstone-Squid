"""Browser authorization routes over the real provider and session service."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import override
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from squid.accounts.domain import IdentityProvider
from squid.auth.application.providers import DiscordOAuthProvider
from squid.auth.application.web import WebSessionService
from squid.config import OAuthClientCredentials
from squid.idempotency import IdempotencyService, PendingRequest, StoredResponse, UnsafeHttpMethod
from tests.unit.api.fakes import TEST_CONFIG, build_app
from tests.unit.auth.application.fakes import AccountServiceRecorder, SessionRepository


@dataclass(slots=True)
class IdempotencyRecorder(IdempotencyService):
    reservations: list[dict[str, object]] = field(default_factory=list)

    @override
    async def reserve(
        self, *, caller: str, key: str, fingerprint: bytes, method: UnsafeHttpMethod, route: str
    ) -> PendingRequest | StoredResponse:
        self.reservations.append(
            {"caller": caller, "key": key, "fingerprint": fingerprint, "method": method, "route": route}
        )
        raise AssertionError("a rejected write must not reserve an idempotency key")


@dataclass
class BrowserLogin:
    client: TestClient
    sessions: SessionRepository
    accounts: AccountServiceRecorder
    auth: WebSessionService
    idempotency: IdempotencyRecorder

    def sign_in(self) -> None:
        self.client.get("/v1/auth/discord")
        assert self.sessions.state is not None
        response = self.client.get(
            "/v1/auth/discord/callback", params={"code": "c", "state": self.sessions.state.state}
        )
        assert response.status_code == 307


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
    idempotency = IdempotencyRecorder()
    config = TEST_CONFIG.model_copy(
        update={"api": TEST_CONFIG.api.model_copy(update={"cors_origins": ("https://catalogue.test",)})}
    )
    app, database = build_app(web_auth=auth, idempotency=idempotency, config=config)
    with TestClient(app, base_url="https://test", follow_redirects=False) as client:
        try:
            yield BrowserLogin(client, sessions, accounts, auth, idempotency)
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


def test_browser_fetches_csrf_token_and_logs_out_only_with_a_matching_header(login: BrowserLogin) -> None:
    login.sign_in()
    session_token = login.client.cookies["__Host-squid_session"]
    csrf_token = login.client.cookies["squid_csrf"]

    response = login.client.get("/v1/auth/csrf", headers={"Origin": "https://catalogue.test"})
    assert response.status_code == 200
    assert response.json() == {"csrf_token": csrf_token}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Access-Control-Allow-Origin"] == "https://catalogue.test"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"

    for csrf_headers in ({}, {"CSRF-Token": "wrong-token"}):
        rejected = login.client.post("/v1/auth/logout", headers={"Idempotency-Key": "logout-request", **csrf_headers})
        assert rejected.status_code == 403
        assert login.sessions.token_hash == login.auth.hash_token(session_token)
    assert login.idempotency.reservations == []

    logout = login.client.post("/v1/auth/logout", headers={"CSRF-Token": csrf_token})
    assert logout.status_code == 204
    assert "__Host-squid_session" not in login.client.cookies
    assert "squid_csrf" not in login.client.cookies
    assert login.sessions.token_hash is None

    # Restoring a stolen cookie cannot revive the revoked session.
    login.client.cookies.set("__Host-squid_session", session_token)
    assert login.client.get("/v1/auth/csrf").status_code == 401


def test_unconfigured_provider_is_not_found(login: BrowserLogin) -> None:
    response = login.client.get("/v1/auth/github")

    assert response.status_code == 404
    assert login.sessions.state is None


@pytest.mark.parametrize("slug", ["Discord", "a" * 33, "-discord", "1discord"])
def test_malformed_slug_cannot_create_authorization_state(login: BrowserLogin, slug: str) -> None:
    response = login.client.get(f"/v1/auth/{slug}")

    assert response.status_code == 422
    assert login.sessions.state is None
