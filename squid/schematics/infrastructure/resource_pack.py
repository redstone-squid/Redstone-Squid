"""Lazy, hash-verified loading for operator-supplied Minecraft resource packs."""

import asyncio
import hashlib
from pathlib import Path

import aiohttp

from squid.schematics.errors import SchematicRenderUnavailableError

MAX_RESOURCE_PACK_BYTES = 256 * 1024 * 1024
PACK_DIGEST_MESSAGE = "The configured resource pack did not match its SHA-256 digest."
PACK_TOO_LARGE_MESSAGE = "The configured resource pack is too large."


class ResourcePackLoader:
    """Load a configured pack once, verifying remote content before caching it."""

    def __init__(
        self,
        *,
        path: Path | None,
        url: str | None,
        expected_sha256: str | None,
        cache_dir: Path,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._path = path
        self._url = url
        self._expected_sha256 = expected_sha256
        self._cache_dir = cache_dir
        self._loaded: tuple[bytes, str] | None = None
        self._lock = asyncio.Lock()
        self._session = session
        self._owns_session = session is None

    async def load(self) -> tuple[bytes, str]:
        """Return the verified pack, fetching a configured URL only on first use."""
        if self._loaded is not None:
            return self._loaded
        async with self._lock:
            if self._loaded is not None:
                return self._loaded
            data = await self._read_source()
            digest = hashlib.sha256(data).hexdigest()
            if self._expected_sha256 is not None and digest != self._expected_sha256:
                raise SchematicRenderUnavailableError(
                    PACK_DIGEST_MESSAGE,
                    context={"expected_sha256": self._expected_sha256, "actual_sha256": digest},
                )
            self._loaded = (data, digest)
            return self._loaded

    async def _read_source(self) -> bytes:
        if self._path is not None:
            try:
                return await asyncio.to_thread(self._path.read_bytes)
            except OSError as exc:
                msg = "The configured resource pack could not be read."
                raise SchematicRenderUnavailableError(
                    msg,
                    context={"pack_path": str(self._path), "error": str(exc)},
                ) from exc
        if self._url is None or self._expected_sha256 is None:
            msg = "No resource pack is configured."
            raise SchematicRenderUnavailableError(
                msg,
                developer_action="Set render_pack_path or render_pack_url (with render_pack_sha256).",
            )

        cached = self._cache_dir / f"{self._expected_sha256}.zip"
        try:
            cached_data = await asyncio.to_thread(cached.read_bytes)
        except FileNotFoundError:
            cached_data = None
        except OSError as exc:
            msg = "The resource-pack cache could not be read."
            raise SchematicRenderUnavailableError(
                msg,
                context={"cache_path": str(cached), "error": str(exc)},
            ) from exc
        if cached_data is not None and hashlib.sha256(cached_data).hexdigest() == self._expected_sha256:
            return cached_data

        session = self._session
        if session is None:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, connect=5, sock_read=45),
                raise_for_status=False,
            )
            self._session = session
        try:
            async with session.get(self._url, allow_redirects=False) as response:
                response.raise_for_status()
                if response.content_length is not None and response.content_length > MAX_RESOURCE_PACK_BYTES:
                    raise SchematicRenderUnavailableError(PACK_TOO_LARGE_MESSAGE)
                data = await response.content.read(MAX_RESOURCE_PACK_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as exc:
            msg = "The configured resource pack could not be downloaded."
            raise SchematicRenderUnavailableError(msg, context={"error": str(exc)}) from exc
        if len(data) > MAX_RESOURCE_PACK_BYTES:
            raise SchematicRenderUnavailableError(PACK_TOO_LARGE_MESSAGE)
        actual_digest = hashlib.sha256(data).hexdigest()
        if actual_digest != self._expected_sha256:
            raise SchematicRenderUnavailableError(
                PACK_DIGEST_MESSAGE,
                context={"expected_sha256": self._expected_sha256, "actual_sha256": actual_digest},
            )

        try:
            await asyncio.to_thread(self._cache_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(cached.write_bytes, data)
        except OSError as exc:
            msg = "The resource-pack cache could not be written."
            raise SchematicRenderUnavailableError(
                msg,
                context={"cache_path": str(cached), "error": str(exc)},
            ) from exc
        return data

    async def aclose(self) -> None:
        """Close the reusable HTTP session when this loader owns it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None
