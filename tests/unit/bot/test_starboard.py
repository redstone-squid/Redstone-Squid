"""Starboard refresh coalescing, owned task lifetimes, and recounts."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import anyio
import discord

from squid.bot.starboard.cog import StarboardCog
from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.reactions.domain import ReactionActor
from squid.runtime import BackgroundTaskSupervisor
from squid.starboard.application import StarboardService
from squid.starboard.models import OriginMessage
from squid_ui_discord.testing import AsyncCallRecorder, MessageHarness


@dataclass
class RecountGuild:
    id: int
    get_member: Callable[[int], Any]
    fetch_member: Callable[[int], Awaitable[Any]]


class RecountService(StarboardService):
    def __init__(self, relevance: Any, recount: Any) -> None:
        self.is_relevant_emoji = AsyncCallRecorder(callback=relevance)
        self.recount = AsyncCallRecorder(callback=recount)


class ScheduledEntries(EntryDebouncer):
    def __init__(self, scheduled: list[tuple[EntryKey, bool]]) -> None:
        self.scheduled = scheduled

    def schedule(self, key: EntryKey, *, force: bool = False) -> None:
        self.scheduled.append((key, force))


async def test_debouncer_coalesces_each_key_and_preserves_force() -> None:
    calls: list[tuple[EntryKey, bool]] = []

    async def callback(key: EntryKey, force: bool) -> None:
        await anyio.lowlevel.checkpoint()
        calls.append((key, force))

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2))
        debouncer.schedule((1, 3))
        debouncer.schedule((1, 2), force=True)
        debouncer.schedule((1, 2))
        await debouncer.drain()

    assert sorted(calls) == [((1, 2), True), ((1, 3), False)]


async def test_activity_during_refresh_runs_once_more_with_latest_state_and_force() -> None:
    started = anyio.Event()
    release = anyio.Event()
    current = 1
    published: list[tuple[int, bool]] = []

    async def callback(key: EntryKey, force: bool) -> None:
        value = current
        if not published:
            started.set()
            await release.wait()
        published.append((value, force))

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2))
        with anyio.fail_after(1):
            await started.wait()
            current = 2
            debouncer.schedule((1, 2), force=True)
            current = 3
            debouncer.schedule((1, 2))
            release.set()
            await debouncer.drain()

        assert published == [(1, False), (3, True)]
        current = 4
        debouncer.schedule((1, 2))
        await debouncer.drain()

    assert published == [(1, False), (3, True), (4, False)]


async def test_close_settles_inflight_refresh_and_cancels_pending_work() -> None:
    calls: list[EntryKey] = []
    started = anyio.Event()
    finished = anyio.Event()

    async def callback(key: EntryKey, force: bool) -> None:
        calls.append(key)
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            finished.set()

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2), force=True)
        with anyio.fail_after(1):
            await started.wait()
            debouncer.schedule((1, 2), force=True)
            debouncer.schedule((1, 3))
            await debouncer.close()

        assert finished.is_set()
        debouncer.schedule((1, 4))
        await debouncer.close()
        await anyio.lowlevel.checkpoint()

    assert calls == [(1, 2)]


async def test_failed_refresh_does_not_block_other_keys_or_later_attempts() -> None:
    calls: list[tuple[EntryKey, bool]] = []
    started = anyio.Event()
    release = anyio.Event()

    async def callback(key: EntryKey, force: bool) -> None:
        calls.append((key, force))
        if key == (1, 2) and force:
            started.set()
            await release.wait()
            raise RuntimeError("Discord unavailable")

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2), force=True)
        debouncer.schedule((1, 3))
        with anyio.fail_after(1):
            await started.wait()
            debouncer.schedule((1, 2))
            release.set()
            await debouncer.drain()

    assert sorted(calls) == [((1, 2), False), ((1, 2), True), ((1, 3), False)]


async def test_recount_reads_relevant_reactions_from_humans_with_their_roles() -> None:
    """Only starboard emojis count, bots are skipped, and uncached members are fetched or dropped."""

    class Reaction:
        def __init__(self, emoji: str, users: list[SimpleNamespace]) -> None:
            self.emoji = emoji
            self._users = users

        async def users(self, *, limit: None) -> AsyncIterator[SimpleNamespace]:
            for user in self._users:
                yield user

    cached = SimpleNamespace(id=1, roles=[SimpleNamespace(id=50)])
    fetched = SimpleNamespace(id=2, roles=[])
    response = MagicMock(status=404, reason="")

    async def fetch_member(user_id: int) -> SimpleNamespace:
        if user_id == fetched.id:
            return fetched
        raise discord.NotFound(response, "left")

    guild = RecountGuild(id=10, get_member={1: cached}.get, fetch_member=fetch_member)
    message = MessageHarness(message_id=100, channel_id=30).source
    message.guild = guild
    message.author = SimpleNamespace(id=40, bot=False)
    message.created_at = datetime.now(UTC)
    message.attachments = []
    message.embeds = []
    message.reactions = [
        Reaction("⭐", [SimpleNamespace(id=1, bot=False), SimpleNamespace(id=9, bot=True)]),
        Reaction("⭐", [SimpleNamespace(id=2, bot=False), SimpleNamespace(id=3, bot=False)]),
        Reaction("👍", [SimpleNamespace(id=1, bot=False)]),
    ]
    recounts: list[tuple[int, tuple[tuple[ReactionActor, str], ...]]] = []
    scheduled: list[tuple[EntryKey, bool]] = []

    async def is_relevant_emoji(guild_id: int, emoji: str) -> bool:
        return emoji == "⭐"

    async def recount(origin: OriginMessage, reactions: tuple[tuple[ReactionActor, str], ...]) -> tuple[EntryKey]:
        recounts.append((origin.id, reactions))
        return ((7, origin.id),)

    cog = cast(Any, StarboardCog).__new__(StarboardCog)
    cog.service = RecountService(is_relevant_emoji, recount)
    cog._debouncer = ScheduledEntries(scheduled)

    assert await cog.recount(message) == ((7, 100),)

    assert recounts == [
        (100, ((ReactionActor(1, 10, frozenset({50})), "⭐"), (ReactionActor(2, 10, frozenset()), "⭐")))
    ]
    assert scheduled == [((7, 100), True)]


async def test_recount_of_a_message_outside_a_guild_counts_nothing() -> None:
    cog = cast(Any, StarboardCog).__new__(StarboardCog)
    # A direct message has no starboards, so neither the service nor Discord should be asked.
    cog.service = None

    assert await cog.recount(MessageHarness(guild_id=None).source) == ()
