"""The notice has to be readable before anyone has an account to read it with."""

from litestar import Request, Response

from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.api.v1.consent import get_notice
from tests.support.litestar import make_request


def request_with_locale(header: str | None) -> Request:
    headers = [(b"accept-language", header.encode())] if header is not None else []
    return make_request({"type": "http", "headers": headers})


async def test_the_notice_is_served_with_the_version_it_names() -> None:
    notice = (await get_notice.fn(request_with_locale(None))).content
    assert notice.version == CURRENT_CONSENT_VERSION
    assert notice.locale == "en"
    assert notice.title
    assert notice.body


async def test_the_notice_is_negotiated_and_echoes_the_locale_it_chose() -> None:
    """Clients need the actual notice locale when negotiation falls back to English."""
    chinese = (await get_notice.fn(request_with_locale("zh-CN"))).content
    fallback = (await get_notice.fn(request_with_locale("fr-FR"))).content
    assert chinese.locale == "zh-CN"
    assert fallback.locale == "en"


async def test_the_notice_response_varies_on_language() -> None:
    """A cache that ignored `Accept-Language` would serve one reader's language to another."""
    response = Response(content=None)
    response = await get_notice.fn(request_with_locale("zh-CN"))
    assert response.headers["Vary"] == "Accept-Language"
    assert "max-age" in response.headers["Cache-Control"]


async def test_the_notice_body_is_paragraphs_of_plain_text() -> None:
    """All clients receive the same paragraph text, without embedded HTML."""
    notice = (await get_notice.fn(request_with_locale(None))).content
    assert "\n\n" in notice.body
    assert "<" not in notice.body
