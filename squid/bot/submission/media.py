"""Swappable artifact mirroring for inferred submissions."""

from typing import Protocol

from squid.bot.utils.uploads import CatboxClient


class MediaMirror(Protocol):
    """Mirror one Discord attachment to durable public storage."""

    async def upload(self, filename: str, data: bytes, content_type: str) -> str:
        """Store the bytes and return their public URL."""
        ...


class CatboxMirror:
    """Mirror media through CatboxClient."""

    def __init__(self, client: CatboxClient) -> None:
        self._client = client

    async def upload(self, filename: str, data: bytes, content_type: str) -> str:
        return await self._client.upload(filename, data, content_type)
