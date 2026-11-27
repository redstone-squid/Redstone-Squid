"""Read channels, roles and users through the shared gateway-less Discord client."""

from collections.abc import Awaitable, Sequence
from typing import Any

import aiohttp
from discord.errors import DiscordException, Forbidden, NotFound

from squid.core.settings.application.discord import (
    ChannelType,
    DiscordChannel,
    DiscordRole,
    DiscordUnavailableError,
    DiscordUser,
)
from squid.discord_rest import DiscordRestClosedError, RestOnlyClient

# Discord's channel type numbers; anything unlisted, such as a directory, is OTHER.
_CHANNEL_TYPES = {
    0: ChannelType.TEXT,
    2: ChannelType.VOICE,
    4: ChannelType.CATEGORY,
    5: ChannelType.ANNOUNCEMENT,
    10: ChannelType.THREAD,
    11: ChannelType.THREAD,
    12: ChannelType.THREAD,
    13: ChannelType.STAGE,
    15: ChannelType.FORUM,
    16: ChannelType.MEDIA,
}


class DiscordRestDirectory:
    """Look up Discord objects with the bot token. Objects the bot cannot see read as missing."""

    def __init__(self, client: RestOnlyClient) -> None:
        self._client = client

    async def guild_channels(self, guild_id: int) -> Sequence[DiscordChannel]:
        payload = await self._request(self._client.http.get_all_guild_channels(guild_id))
        return [_channel(item) for item in sorted(payload or (), key=lambda item: int(item.get("position", 0)))]

    async def guild_roles(self, guild_id: int) -> Sequence[DiscordRole]:
        payload = await self._request(self._client.http.get_roles(guild_id))
        # Discord lists roles lowest first; pickers show the highest first, as Discord's own UI does.
        ordered = sorted(payload or (), key=lambda item: int(item.get("position", 0)), reverse=True)
        return [DiscordRole(id=int(item["id"]), name=str(item["name"])) for item in ordered]

    async def channel(self, channel_id: int) -> DiscordChannel | None:
        payload = await self._request(self._client.http.get_channel(channel_id))
        return None if payload is None else _channel(payload)

    async def user(self, user_id: int) -> DiscordUser | None:
        payload = await self._request(self._client.http.get_user(user_id))
        if payload is None:
            return None
        return DiscordUser(id=int(payload["id"]), name=str(payload.get("global_name") or payload["username"]))

    @staticmethod
    async def _request(call: Awaitable[Any]) -> Any:
        """Return the payload, None when Discord says the object is missing or hidden."""
        try:
            return await call
        except NotFound, Forbidden:
            return None
        except (DiscordRestClosedError, DiscordException, TimeoutError, aiohttp.ClientError, OSError) as error:
            raise DiscordUnavailableError from error


def _channel(payload: Any) -> DiscordChannel:
    return DiscordChannel(
        id=int(payload["id"]),
        name=str(payload.get("name") or ""),
        type=_CHANNEL_TYPES.get(int(payload.get("type", -1)), ChannelType.OTHER),
    )
