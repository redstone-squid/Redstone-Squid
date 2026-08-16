"""Browser-session route shape.

`/v1/auth/{provider}` is a template and `/v1/auth/discord` is one instance of it, so
`SQUID_OAUTH_REDIRECT_URI` needs no coordinated change and the frontend's URLs keep
working byte-identically.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from squid.core.errors import NotFoundError
from tests.unit.api.fakes import build_app


def web_auth(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "authorize_url": AsyncMock(return_value="https://discord.example/authorize?state=abc"),
        "callback": AsyncMock(return_value=("session-token", "/account")),
        "logout": AsyncMock(),
        "authenticate": AsyncMock(return_value=None),
    }
    return SimpleNamespace(**(defaults | overrides))


def test_the_discord_paths_still_resolve_and_carry_their_slug() -> None:
    """The instance of the template that already exists in the wild."""
    auth = web_auth()
    app, _ = build_app(web_auth=auth)

    with TestClient(app) as client:
        response = client.get("/v1/auth/discord", params={"redirect_to": "/account"}, follow_redirects=False)

    assert response.status_code == 307
    auth.authorize_url.assert_awaited_once_with("discord", "/account")


def test_the_callback_passes_its_slug_through() -> None:
    auth = web_auth()
    app, _ = build_app(web_auth=auth)

    with TestClient(app) as client:
        response = client.get("/v1/auth/discord/callback", params={"code": "c", "state": "s"}, follow_redirects=False)

    assert response.status_code == 307
    assert auth.callback.await_args is not None
    assert auth.callback.await_args.args[:3] == ("discord", "c", "s")


def test_the_templated_route_does_not_swallow_the_csrf_endpoint() -> None:
    """FastAPI matches in declaration order, so this failure would be silent.

    `GET /v1/auth/{provider}` declared above `GET /v1/auth/csrf` would answer the CSRF
    request as a provider lookup -- a plausible-looking 404 rather than an obvious break.
    """
    auth = web_auth()
    app, _ = build_app(web_auth=auth)

    with TestClient(app) as client:
        response = client.get("/v1/auth/csrf")

    # 401 because this client carries no session, which is the CSRF route answering. A
    # 404 here would mean the templated route had taken the path.
    assert response.status_code == 401
    auth.authorize_url.assert_not_awaited()


def test_an_unconfigured_provider_is_a_404_not_a_credential_failure() -> None:
    auth = web_auth(authorize_url=AsyncMock(side_effect=NotFoundError(context={"provider": "github"})))
    app, _ = build_app(web_auth=auth)

    with TestClient(app) as client:
        response = client.get("/v1/auth/github", follow_redirects=False)

    assert response.status_code == 404


@pytest.mark.parametrize("slug", ["Discord", "a" * 33, "-discord", "1discord"])
def test_a_malformed_slug_never_reaches_the_service(slug: str) -> None:
    auth = web_auth()
    app, _ = build_app(web_auth=auth)

    with TestClient(app) as client:
        response = client.get(f"/v1/auth/{slug}", follow_redirects=False)

    assert response.status_code in {404, 422}
    auth.authorize_url.assert_not_awaited()
