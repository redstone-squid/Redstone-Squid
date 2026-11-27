"""Mapping Discord REST answers onto the settings directory."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from discord.errors import Forbidden, NotFound

from squid.core.settings.application.discord import ChannelType, DiscordUnavailableError
from squid.core.settings.infrastructure.discord_rest import DiscordRestDirectory


def directory(**http: AsyncMock) -> DiscordRestDirectory:
    return DiscordRestDirectory(cast(Any, SimpleNamespace(http=SimpleNamespace(**http))))


def refusal(error: type[NotFound | Forbidden]) -> AsyncMock:
    return AsyncMock(side_effect=error(MagicMock(status=404 if error is NotFound else 403, reason=""), "refused"))


async def test_hidden_and_missing_objects_read_as_absent() -> None:
    lookups = directory(get_channel=refusal(Forbidden), get_user=refusal(NotFound))

    assert await lookups.channel(1) is None
    assert await lookups.user(1) is None


async def test_network_failures_mean_discord_is_unavailable() -> None:
    with pytest.raises(DiscordUnavailableError):
        await directory(get_roles=AsyncMock(side_effect=aiohttp.ClientConnectionError())).guild_roles(1)


async def test_listings_follow_discord_order_and_map_channel_types() -> None:
    lookups = directory(
        get_all_guild_channels=AsyncMock(
            return_value=[
                {"id": "2", "name": "rules", "type": 0, "position": 1},
                {"id": "1", "name": "Info", "type": 4, "position": 0},
                {"id": "3", "name": "directory", "type": 14, "position": 2},
            ]
        ),
        get_roles=AsyncMock(
            return_value=[
                {"id": "10", "name": "@everyone", "position": 0},
                {"id": "11", "name": "Staff", "position": 1},
            ]
        ),
    )

    channels = await lookups.guild_channels(1)
    roles = await lookups.guild_roles(1)

    assert [(channel.name, channel.type) for channel in channels] == [
        ("Info", ChannelType.CATEGORY),
        ("rules", ChannelType.TEXT),
        ("directory", ChannelType.OTHER),
    ]
    assert [role.name for role in roles] == ["Staff", "@everyone"]
