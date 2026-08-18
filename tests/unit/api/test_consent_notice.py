"""The notice has to be readable before anyone has an account to read it with."""

from starlette.requests import Request
from starlette.responses import Response

from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.api.v1.consent import get_notice


def request_with_locale(header: str | None) -> Request:
    headers = [(b"accept-language", header.encode())] if header is not None else []
    return Request({"type": "http", "headers": headers})


async def test_the_notice_is_served_with_the_version_it_names() -> None:
    notice = await get_notice(request_with_locale(None), Response())

    assert notice.version == CURRENT_CONSENT_VERSION
    assert notice.locale == "en"
    assert notice.title
    assert notice.body


async def test_the_notice_is_negotiated_and_echoes_the_locale_it_chose() -> None:
    """Echoed rather than assumed: a client asking for an unsupported language needs to know
    it was given the fallback, because it is about to record consent to whatever it displays."""
    chinese = await get_notice(request_with_locale("zh-CN"), Response())
    fallback = await get_notice(request_with_locale("fr-FR"), Response())

    assert chinese.locale == "zh-CN"
    assert fallback.locale == "en"


async def test_the_notice_response_varies_on_language() -> None:
    """A cache that ignored `Accept-Language` would serve one reader's language to another."""
    response = Response()

    await get_notice(request_with_locale("zh-CN"), response)

    assert response.headers["Vary"] == "Accept-Language"
    assert "max-age" in response.headers["Cache-Control"]


async def test_the_notice_body_is_paragraphs_of_plain_text() -> None:
    """Rendered into a Discord card, an HTML page and a terminal; only one could parse markup."""
    notice = await get_notice(request_with_locale(None), Response())

    assert "\n\n" in notice.body
    assert "<" not in notice.body
