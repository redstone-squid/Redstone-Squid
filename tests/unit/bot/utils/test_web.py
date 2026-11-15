"""Trusted media preview policy tests."""

from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import Mock

import aiohttp
import pytest

from squid.bot.utils.web import MAX_PREVIEW_PAGE_BYTES, MediaPreviewClient, _preview_response, is_trusted_preview_url


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.discordapp.com/file.png",
        "https://example.com/file.png",
        "https://cdn.discordapp.com@127.0.0.1/file.png",
        "https://cdn.discordapp.com:8443/file.png",
        "https://127.0.0.1/file.png",
        "not a url",
    ],
)
def test_preview_policy_rejects_untrusted_and_ambiguous_urls(url: str) -> None:
    assert is_trusted_preview_url(url) is False


def test_preview_policy_accepts_discord_https_origins() -> None:
    assert is_trusted_preview_url("https://cdn.discordapp.com/attachments/1/2/image.png") is True
    assert is_trusted_preview_url("https://media.discordapp.net/attachments/1/2/image.png") is True


async def test_untrusted_preview_returns_empty_without_network_io() -> None:
    session = Mock()
    client = MediaPreviewClient(cast(aiohttp.ClientSession, session))

    preview = await client.get("https://example.com/private")

    assert preview == {"title": None, "description": None, "image": None, "video": None, "site_name": None, "url": None}
    session.get.assert_not_called()


class ChunkedContent:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            for start in range(0, len(chunk), size):
                yield chunk[start : start + size]


@pytest.mark.parametrize("host", ["imgur.com", "i.imgur.com"])
def test_preview_policy_accepts_imgur_https_origins(host: str) -> None:
    assert is_trusted_preview_url(f"https://{host}/example")


async def test_imgur_preview_reads_metadata_after_the_first_network_chunk() -> None:
    response = Mock(
        status=200,
        headers={"Content-Type": "text/html"},
        content_length=None,
        charset="utf-8",
        content=ChunkedContent(
            (
                b"<html><head>",
                b'<meta property="og:image" content="https://i.imgur.com/YQ2b2aO.jpg?fbplay">',
                b'<meta property="og:video" content="https://i.imgur.com/YQ2b2aO.mp4">',
                b"</head></html>",
            )
        ),
    )

    preview = await _preview_response("https://imgur.com/a/SYA1OuC", cast(aiohttp.ClientResponse, response))

    assert preview["image"] == "https://i.imgur.com/YQ2b2aO.jpg?fbplay"
    assert preview["video"] == "https://i.imgur.com/YQ2b2aO.mp4"


async def test_chunked_preview_stops_at_the_page_size_limit() -> None:
    response = Mock(
        status=200,
        headers={"Content-Type": "text/html"},
        content_length=None,
        charset="utf-8",
        content=ChunkedContent((b"x" * MAX_PREVIEW_PAGE_BYTES, b"x")),
    )

    preview = await _preview_response("https://imgur.com/a/example", cast(aiohttp.ClientResponse, response))

    assert preview["image"] is None
    assert preview["video"] is None
