"""Bounded, process-owned Mojang account lookup adapter."""

import json
from typing import Any
from uuid import UUID

import aiohttp

from squid.accounts.errors import MinecraftServiceUnavailableError

MAX_PROFILE_RESPONSE_BYTES = 64 * 1024


class MojangClient:
    """Resolve current Minecraft names through one reusable HTTP session."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    async def get_username(self, minecraft_uuid: UUID) -> str | None:
        """Return the current username for a Minecraft UUID."""
        session = self._session
        if session is None:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10, connect=3, sock_read=5),
                raise_for_status=False,
            )
            self._session = session
        url = f"https://sessionserver.mojang.com/session/minecraft/profile/{minecraft_uuid!s}"
        try:
            async with session.get(url, allow_redirects=False) as response:
                if response.status == 204:
                    return None
                if response.status != 200:
                    raise _service_error(minecraft_uuid, response.status)
                payload = await _read_bounded(response, MAX_PROFILE_RESPONSE_BYTES)
        except MinecraftServiceUnavailableError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise _service_error(minecraft_uuid, None) from error

        try:
            document: Any = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise _service_error(minecraft_uuid, 200) from error
        if not isinstance(document, dict) or not isinstance(document.get("name"), str):
            raise _service_error(minecraft_uuid, 200)
        return document["name"]

    async def aclose(self) -> None:
        """Close the reusable HTTP session when this client owns it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None


async def _read_bounded(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    if response.content_length is not None and response.content_length > max_bytes:
        msg = "The Mojang profile response exceeded its size limit."
        raise MinecraftServiceUnavailableError(msg)
    payload = await response.content.read(max_bytes + 1)
    if len(payload) > max_bytes:
        msg = "The Mojang profile response exceeded its size limit."
        raise MinecraftServiceUnavailableError(msg)
    return payload


def _service_error(minecraft_uuid: UUID, status: int | None) -> MinecraftServiceUnavailableError:
    msg = "The Mojang session service is temporarily unavailable."
    context: dict[str, str | int] = {"minecraft_uuid": str(minecraft_uuid)}
    if status is not None:
        context["status"] = status
    return MinecraftServiceUnavailableError(msg, context=context)
