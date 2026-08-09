"""Trusted media preview policy tests."""

from typing import cast
from unittest.mock import Mock

import aiohttp
import pytest

from squid.bot.utils.web import MediaPreviewClient, is_trusted_preview_url


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

    assert preview == {"title": None, "description": None, "image": None, "site_name": None, "url": None}
    session.get.assert_not_called()
