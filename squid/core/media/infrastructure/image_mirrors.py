"""Copy linked build images before their source URLs expire."""

import ipaddress
import socket
import tempfile
from pathlib import Path

import aiohttp
import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from yarl import URL

from squid.core.actors import ActingContext
from squid.core.media.application.commands import MediaProcessingRequest
from squid.core.media.application.services import MediaProcessingService
from squid.core.media.domain import MediaKind
from squid.core.media.errors import InvalidMediaError, MediaLimitExceededError, MediaProcessingError

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def public_socket(addr_info: tuple) -> socket.socket:
    """Validate the actual connection address, including numeric hosts and redirects."""
    family, kind, proto, _, sockaddr = addr_info
    address = ipaddress.ip_address(sockaddr[0])
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_global or address.is_multicast:
        msg = "Image host requires a public IPv4 address"
        raise ValueError(msg)
    return socket.socket(family, kind, proto)


async def fetch_image(source: str, path: Path) -> None:
    connector = aiohttp.TCPConnector(family=socket.AF_INET, socket_factory=public_socket)
    async with aiohttp.ClientSession(
        connector=connector, auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar()
    ) as session:
        url = URL(source)
        with anyio.fail_after(20):
            for _ in range(5):
                if url.scheme not in {"http", "https"} or url.user is not None or url.port not in {80, 443}:
                    msg = "Unsupported image URL"
                    raise ValueError(msg)
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308} and "Location" in response.headers:
                        url = url.join(URL(response.headers["Location"]))
                        continue
                    if response.status != 200:
                        msg = "Image request failed"
                        raise ValueError(msg)
                    size = 0
                    async with await anyio.open_file(path, "wb") as output:
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                msg = "Image exceeds mirror limit"
                                raise ValueError(msg)
                            await output.write(chunk)
                    async with await anyio.open_file(path, "rb") as saved:
                        header = await saved.read(12)
                    if not (
                        header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
                        or (header.startswith(b"RIFF") and header[8:] == b"WEBP")
                    ):
                        msg = "Unsupported image signature"
                        raise ValueError(msg)
                    return
    msg = "Too many image redirects"
    raise ValueError(msg)


class BuildImageMirrorWorker:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], processing: MediaProcessingService) -> None:
        self.sessions = sessions
        self.processing = processing

    async def process(self, *, context: ActingContext) -> None:
        # A claim expires after a crash. The token fences late workers and link deletion cascades.
        async with self.sessions.begin() as session:
            row = (
                await session.execute(
                    text("""
                INSERT INTO build_image_mirrors (build_id,url)
                SELECT l.build_id,l.url FROM build_links l
                LEFT JOIN build_image_mirrors m USING (build_id,url)
                WHERE l.media_type='image' AND (m.id IS NULL OR
                    (m.content IS NULL AND m.attempts<3 AND m.retry_at<=now()))
                ORDER BY l.build_id DESC LIMIT 1
                ON CONFLICT (build_id,url) DO UPDATE SET attempts=build_image_mirrors.attempts+1,
                    retry_at=now()+interval '10 minutes'
                WHERE build_image_mirrors.content IS NULL AND build_image_mirrors.retry_at<=now()
                RETURNING id,url,attempts
            """)
                )
            ).first()
        if row is None:
            return
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source"
                output = Path(directory) / "output"
                await fetch_image(row.url, source)
                result = await self.processing.process(
                    MediaProcessingRequest(MediaKind.IMAGE, source, output), context=context
                )
                content = await anyio.Path(output).read_bytes()
        except (
            ValueError,
            aiohttp.ClientError,
            TimeoutError,
            InvalidMediaError,
            MediaLimitExceededError,
            MediaProcessingError,
        ):
            return
        async with self.sessions.begin() as session:
            await session.execute(
                text("""
                UPDATE build_image_mirrors SET content=:content, content_type=:mime
                WHERE id=:id AND attempts=:attempts
            """),
                dict(content=content, mime=result.report.output.content_type, id=row.id, attempts=row.attempts),
            )
