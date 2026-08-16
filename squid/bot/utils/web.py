"""Constrained media-preview fetching for trusted Discord origins."""

import asyncio
import io
import logging
import mimetypes
from collections import OrderedDict
from typing import TypedDict, cast
from urllib.parse import urlsplit

import aiohttp
import bs4

logger = logging.getLogger(__name__)

TRUSTED_PREVIEW_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
MAX_PREVIEW_PAGE_BYTES = 1024 * 1024
MAX_PREVIEW_CACHE_ENTRIES = 256
PREVIEW_USER_AGENT = "Redstone-Squid/1.0 (+https://github.com/Redstone-Squid/Redstone-Squid)"
MAX_VIDEO_FRAME_BYTES = 4 * 1024 * 1024


class Preview(TypedDict):
    title: str | None
    description: str | None
    image: str | io.BytesIO | None
    site_name: str | None
    url: str | None


class MediaPreviewClient:
    """Resolve previews only from allowlisted HTTPS origins with bounded responses."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None
        self._cache: OrderedDict[str, Preview] = OrderedDict()

    async def get(self, url: str) -> Preview:
        """Return cached metadata or an empty preview when the origin is not trusted."""
        cached = self._cache.get(url)
        if cached is not None:
            self._cache.move_to_end(url)
            return cast(Preview, dict(cached))
        if not is_trusted_preview_url(url):
            return _empty_preview()

        session = self._session
        if session is None:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10, connect=3, sock_read=5),
                raise_for_status=False,
            )
            self._session = session
        try:
            async with session.get(
                url,
                headers={"User-Agent": PREVIEW_USER_AGENT},
                allow_redirects=False,
            ) as response:
                preview = await _preview_response(url, response)
        except aiohttp.ClientError, TimeoutError:
            logger.debug("Trusted media preview request failed", exc_info=True)
            preview = _empty_preview()
        self._cache[url] = preview
        self._cache.move_to_end(url)
        if len(self._cache) > MAX_PREVIEW_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return cast(Preview, dict(preview))

    async def aclose(self) -> None:
        """Close the reusable HTTP session when this client owns it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None


async def extract_first_frame(video_data: bytes, *, timeout_seconds: float = 10) -> io.BytesIO:
    """Extract one bounded frame from already-downloaded bytes, never a remote URL."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-vf",
        "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            output, _error = await process.communicate(input=video_data)
    except asyncio.CancelledError:
        # A cancellation from outside — bot shutdown, or a view timing out — would
        # otherwise leave ffmpeg running with nobody reading its pipes.
        process.kill()
        await process.wait()
        raise
    except TimeoutError:
        process.kill()
        await process.wait()
        msg = "Video frame extraction exceeded its deadline."
        raise RuntimeError(msg) from None
    if process.returncode != 0:
        msg = "Video frame extraction failed."
        raise RuntimeError(msg)
    if len(output) > MAX_VIDEO_FRAME_BYTES:
        msg = "Video frame extraction exceeded its output limit."
        raise RuntimeError(msg)
    return io.BytesIO(output)


def is_trusted_preview_url(url: str) -> bool:
    """Return whether a URL is safe for the server itself to request."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in TRUSTED_PREVIEW_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


async def _preview_response(url: str, response: aiohttp.ClientResponse) -> Preview:
    if response.status != 200:
        return _empty_preview()
    content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if not content_type:
        content_type = mimetypes.guess_type(url, strict=False)[0] or "application/octet-stream"
    if content_type.startswith("image/"):
        preview = _empty_preview()
        preview["image"] = url
        preview["url"] = url
        return preview
    if content_type != "text/html":
        return _empty_preview()
    if response.content_length is not None and response.content_length > MAX_PREVIEW_PAGE_BYTES:
        return _empty_preview()
    payload = await response.content.read(MAX_PREVIEW_PAGE_BYTES + 1)
    if len(payload) > MAX_PREVIEW_PAGE_BYTES:
        return _empty_preview()
    return _parse_page(url, payload.decode(response.charset or "utf-8", errors="replace"))


def _parse_page(url: str, page_text: str) -> Preview:
    preview = _empty_preview()
    soup = bs4.BeautifulSoup(page_text, "html.parser")

    def meta_content(property_name: str, attribute_type: str = "property") -> str | None:
        tag = soup.find("meta", attrs={attribute_type: property_name})
        if not isinstance(tag, bs4.element.Tag):
            return None
        content = tag.get("content")
        return content.strip() if isinstance(content, str) else None

    preview["title"] = meta_content("og:title") or meta_content("twitter:title", "name")
    preview["description"] = meta_content("og:description") or meta_content("twitter:description", "name")
    image = meta_content("og:image") or meta_content("twitter:image", "name")
    preview["image"] = image if image is not None and is_trusted_preview_url(image) else None
    preview["site_name"] = meta_content("og:site_name") or urlsplit(url).hostname
    canonical_url = meta_content("og:url")
    preview["url"] = canonical_url if canonical_url is not None and is_trusted_preview_url(canonical_url) else url
    if preview["title"] is None and soup.title is not None and soup.title.string is not None:
        preview["title"] = soup.title.string.strip()
    if preview["description"] is None:
        preview["description"] = meta_content("description", "name")
    return preview


def _empty_preview() -> Preview:
    return {"title": None, "description": None, "image": None, "site_name": None, "url": None}
