"""Swappable artifact mirroring for inferred submissions."""

from typing import Protocol

from squid.bot.utils.uploads import upload_to_catbox
from squid.config import CatboxConfig


class MediaMirror(Protocol):
    """Mirror one Discord attachment to durable public storage."""

    async def upload(self, filename: str, data: bytes, content_type: str) -> str: ...


class CatboxMirror:
    """Mirror media through the existing Catbox uploader."""

    def __init__(self, config: CatboxConfig) -> None:
        self._config = config

    async def upload(self, filename: str, data: bytes, content_type: str) -> str:
        """Upload bytes and return their public URL."""
        return await upload_to_catbox(filename, data, content_type, self._config)
