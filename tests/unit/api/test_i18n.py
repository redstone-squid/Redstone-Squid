"""REST API locale negotiation tests."""

import httpx
import pytest
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from squid.api.i18n import LocaleContextMiddleware, _parse_accept_language, locale_for_request
from squid_ui.text import current_localization


def _request_with_header(header: str | None) -> Request:
    headers = [(b"accept-language", header.encode())] if header is not None else []
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def test_parse_accept_language_orders_by_quality() -> None:
    assert _parse_accept_language("fr;q=0.5, en;q=0.9, de") == ["de", "en", "fr"]


def test_parse_accept_language_ignores_wildcard() -> None:
    assert _parse_accept_language("*, en") == ["en"]


def test_parse_accept_language_handles_malformed_quality() -> None:
    assert _parse_accept_language("en;q=not-a-number") == ["en"]


def test_locale_for_request_defaults_without_header() -> None:
    assert locale_for_request(_request_with_header(None)) == "en"


def test_locale_for_request_matches_supported_locale() -> None:
    assert locale_for_request(_request_with_header("zh-CN")) == "zh-CN"


def test_locale_for_request_negotiates_language_only_match() -> None:
    assert locale_for_request(_request_with_header("zh-TW,zh;q=0.9")) == "zh-CN"


def test_locale_for_request_falls_back_for_unsupported_locale() -> None:
    assert locale_for_request(_request_with_header("fr-FR")) == "en"


@pytest.mark.asyncio
async def test_locale_middleware_binds_and_restores_the_request_localization() -> None:
    seen: list[str | None] = []
    original = current_localization()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, receive, send
        seen.append(current_localization().locale)

    middleware = LocaleContextMiddleware(app)
    scope = {"type": "http", "headers": [(b"accept-language", b"zh-CN")]}

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        del message

    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert seen == ["zh-CN"]
    assert current_localization() is original


def test_verify_endpoint_reflects_negotiated_content_language(client: httpx.Client) -> None:
    resp = client.post(
        "/verify",
        json={"uuid": "11111111-1111-1111-1111-111111111111"},
        headers={"Authorization": "wrong-secret", "Accept-Language": "zh-CN"},
    )
    assert resp.status_code == 401
    assert resp.headers["content-language"] == "zh-CN"


def test_verify_endpoint_defaults_content_language(client: httpx.Client) -> None:
    resp = client.post(
        "/verify",
        json={"uuid": "11111111-1111-1111-1111-111111111111"},
        headers={"Authorization": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert resp.headers["content-language"] == "en"
