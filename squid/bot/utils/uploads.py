"""Bounded Discord transport file-upload clients."""

import io
from urllib.parse import urlsplit

import aiohttp

from squid.config import CatboxConfig

CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
MAX_CATBOX_RESPONSE_BYTES = 2 * 1024


class MediaUploadError(OSError):
    """A configured media host rejected or malformed an upload."""


class CatboxClient:
    """Upload public media through one reusable, explicitly bounded session."""

    def __init__(self, config: CatboxConfig, session: aiohttp.ClientSession | None = None) -> None:
        self._config = config
        self._session = session
        self._owns_session = session is None

    async def upload(self, filename: str, file: bytes | io.BytesIO, mimetype: str) -> str:
        """Upload bytes and return a validated Catbox file URL."""
        session = self._session
        if session is None:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45, connect=5, sock_read=30),
                raise_for_status=False,
            )
            self._session = session

        data = aiohttp.FormData()
        data.add_field("reqtype", "fileupload")
        if self._config.user_hash is not None:
            data.add_field("userhash", self._config.user_hash.get_secret_value())
        data.add_field("fileToUpload", file, filename=filename, content_type=mimetype)

        try:
            async with session.post(CATBOX_UPLOAD_URL, data=data, allow_redirects=False) as response:
                status = response.status
                payload = await response.content.read(MAX_CATBOX_RESPONSE_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as error:
            msg = "The media host is temporarily unavailable."
            raise MediaUploadError(msg) from error
        if status != 200:
            msg = f"The media host rejected the upload with status {status}."
            raise MediaUploadError(msg)
        if len(payload) > MAX_CATBOX_RESPONSE_BYTES:
            msg = "The media host returned an oversized response."
            raise MediaUploadError(msg)
        try:
            response_text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            msg = "The media host returned an invalid response."
            raise MediaUploadError(msg) from error
        return validate_catbox_url(response_text)

    async def aclose(self) -> None:
        """Close the reusable HTTP session when this client owns it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None


def validate_catbox_url(value: str) -> str:
    """Accept only Catbox's HTTPS file origin, never an error string or active URL."""
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "files.catbox.moe" or not parsed.path.strip("/"):
        msg = "The media host returned an invalid file URL."
        raise MediaUploadError(msg)
    if parsed.username is not None or parsed.password is not None or parsed.port not in {None, 443}:
        msg = "The media host returned an invalid file URL."
        raise MediaUploadError(msg)
    return url
