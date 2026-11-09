"""Guild presence and channel repair from the gateway cache."""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import discord
import pytest

from squid.bot.guild_state import GuildStateCog
from squid.settings.application import SettingsService
from squid.settings.domain import Setting
from squid.starboard.application import StarboardService
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


@dataclass
class FakeSettings(SettingsService):
    referenced: dict[int, frozenset[int]] = field(default_factory=dict)
    presence: list[frozenset[int]] = field(default_factory=list)
    forgotten: list[tuple[int, frozenset[int]]] = field(default_factory=list)

    async def sync_presence(self, present_server_ids: Iterable[int]) -> tuple[list[int], list[int]]:
        self.presence.append(frozenset(present_server_ids))
        return [], []

    async def referenced_channels(self, server_id: int) -> frozenset[int]:
        return self.referenced.get(server_id, frozenset())

    async def forget_channels(self, server_id: int, channel_ids: Collection[int]) -> tuple[Setting, ...]:
        self.forgotten.append((server_id, frozenset(channel_ids)))
        return ()


@dataclass
class FakeStarboards(StarboardService):
    referenced: dict[int, frozenset[int]] = field(default_factory=dict)
    forgotten: list[tuple[int, frozenset[int]]] = field(default_factory=list)

    async def referenced_channels(self, guild_id: int) -> frozenset[int]:
        return self.referenced.get(guild_id, frozenset())

    async def forget_channels(self, guild_id: int, channel_ids: Collection[int]) -> None:
        self.forgotten.append((guild_id, frozenset(channel_ids)))


def _http_error(status: int) -> discord.HTTPException:
    response = MagicMock(status=status, reason="")
    match status:
        case 404:
            return discord.NotFound(response, "gone")
        case 403:
            return discord.Forbidden(response, "hidden")
        case _:
            return discord.HTTPException(response, "failed")


class FakeGuild:
    def __init__(self, guild_id: int, channels: Collection[int], *, unavailable: bool = False) -> None:
        self.id = guild_id
        self.unavailable = unavailable
        self._channels = set(channels)

    def get_channel_or_thread(self, channel_id: int) -> object | None:
        return object() if channel_id in self._channels else None


def _cog(
    settings: FakeSettings, starboards: FakeStarboards, *, guilds: list[FakeGuild], fetch: dict[int, int]
) -> tuple[GuildStateCog[Any], list[int]]:
    """Build the cog over fakes. `fetch` maps a channel ID to the HTTP status Discord answers with."""
    fetched: list[int] = []

    async def fetch_channel(channel_id: int) -> object:
        fetched.append(channel_id)
        status = fetch.get(channel_id, 200)
        if status != 200:
            raise _http_error(status)
        return object()

    bot = make_layout_bot(
        services=bot_services(settings=settings, starboards=starboards),
        guilds=guilds,
        fetch_channel=fetch_channel,
    )
    return GuildStateCog(cast(Any, bot)), fetched


async def test_ready_replaces_presence_with_the_sessions_guilds() -> None:
    settings = FakeSettings()
    cog, _ = _cog(settings, FakeStarboards(), guilds=[FakeGuild(1, ()), FakeGuild(2, ())], fetch={})

    await cog.on_ready()

    assert settings.presence == [frozenset({1, 2})]


async def test_available_guild_forgets_only_channels_discord_confirms_deleted() -> None:
    # 10 is cached. 11 is missing and gone, 12 is an archived thread, 13 is hidden, 14 errors.
    settings = FakeSettings(referenced={1: frozenset({10, 11, 12})})
    starboards = FakeStarboards(referenced={1: frozenset({11, 13, 14})})
    cog, fetched = _cog(settings, starboards, guilds=[], fetch={11: 404, 13: 403, 14: 500})

    await cog.on_guild_available(cast(discord.Guild, FakeGuild(1, {10})))

    assert sorted(fetched) == [11, 12, 13, 14]
    assert settings.forgotten == [(1, frozenset({11}))]
    assert starboards.forgotten == [(1, frozenset({11}))]


async def test_unavailable_guild_is_not_checked_because_its_channels_are_not_cached() -> None:
    settings = FakeSettings(referenced={1: frozenset({10})})
    cog, fetched = _cog(settings, FakeStarboards(), guilds=[], fetch={10: 404})

    await cog.on_guild_available(cast(discord.Guild, FakeGuild(1, (), unavailable=True)))

    assert (fetched, settings.forgotten) == ([], [])


@pytest.mark.parametrize("thread", [False, True])
async def test_deleted_channel_or_thread_is_forgotten_everywhere(thread: bool) -> None:
    settings, starboards = FakeSettings(), FakeStarboards()
    cog, _ = _cog(settings, starboards, guilds=[], fetch={})

    if thread:
        await cog.on_raw_thread_delete(cast(discord.RawThreadDeleteEvent, SimpleNamespace(guild_id=1, thread_id=5)))
    else:
        await cog.on_guild_channel_delete(cast(discord.abc.GuildChannel, SimpleNamespace(id=5, guild=FakeGuild(1, ()))))

    assert settings.forgotten == starboards.forgotten == [(1, frozenset({5}))]
