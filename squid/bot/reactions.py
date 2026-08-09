"""Centralized Discord raw-reaction dispatch."""

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, override

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    """A normalized reaction action with memoized Discord lookups."""

    payload: discord.RawReactionActionEvent
    emoji: str
    member: discord.Member | None
    _bot: "squid.bot.app.RedstoneSquid" = field(repr=False, compare=False)
    _message_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _message: discord.Message | None = field(default=None, init=False, repr=False, compare=False)
    _message_loaded: bool = field(default=False, init=False, repr=False, compare=False)
    _member_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _member_loaded: bool = field(default=False, init=False, repr=False, compare=False)

    async def message(self) -> discord.Message | None:
        """Fetch the reacted-to message at most once across all subscribers."""
        async with self._message_lock:
            if self._message_loaded:
                return self._message
            message = await self._bot.get_or_fetch_message(
                self.payload.channel_id, self.payload.message_id, untrack_if_missing=False
            )
            object.__setattr__(self, "_message", message)
            object.__setattr__(self, "_message_loaded", True)
            return message

    async def resolve_member(self) -> discord.Member | None:
        """Resolve the reacting guild member at most once, only when a subscriber needs it."""
        async with self._member_lock:
            if self._member_loaded:
                return self.member
            member = self.member
            guild = self._bot.get_guild(self.payload.guild_id) if self.payload.guild_id is not None else None
            if member is None and guild is not None:
                member = guild.get_member(self.payload.user_id)
            if member is None and guild is not None:
                with contextlib.suppress(discord.NotFound, discord.Forbidden):
                    member = await guild.fetch_member(self.payload.user_id)
            object.__setattr__(self, "member", member)
            object.__setattr__(self, "_member_loaded", True)
            return member


@dataclass(frozen=True, slots=True)
class ReactionClearEvent:
    """A normalized clear event, optionally scoped to one emoji."""

    payload: discord.RawReactionClearEvent | discord.RawReactionClearEmojiEvent
    emoji: str | None = None


class ReactionSubscriber(Protocol):
    """Receive normalized raw-reaction events from the bot router."""

    async def on_reaction_add(self, event: ReactionEvent) -> None: ...

    async def on_reaction_remove(self, event: ReactionEvent) -> None: ...

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None: ...

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None: ...


class ReactionRouter:
    """Fan out raw reactions with shared resolution and per-subscriber isolation."""

    def __init__(self, bot: "squid.bot.app.RedstoneSquid") -> None:
        self._bot = bot
        self._subscribers: set[ReactionSubscriber] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, subscriber: ReactionSubscriber) -> None:
        self._subscribers.add(subscriber)

    def unsubscribe(self, subscriber: ReactionSubscriber) -> None:
        self._subscribers.discard(subscriber)

    async def dispatch_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("on_reaction_add", payload)

    async def dispatch_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("on_reaction_remove", payload)

    async def dispatch_clear(self, payload: discord.RawReactionClearEvent) -> None:
        self._dispatch("on_reaction_clear", ReactionClearEvent(payload))

    async def dispatch_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        self._dispatch("on_reaction_clear_emoji", ReactionClearEvent(payload, str(payload.emoji)))

    async def close(self) -> None:
        """Wait for already-dispatched subscriber work during shutdown."""
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _dispatch_action(self, method: str, payload: discord.RawReactionActionEvent) -> None:
        member = payload.member
        if member is None and payload.guild_id is not None:
            guild = self._bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id) if guild is not None else None
        self._dispatch(method, ReactionEvent(payload, str(payload.emoji), member, self._bot))

    def _dispatch(self, method: str, event: ReactionEvent | ReactionClearEvent) -> None:
        for subscriber in tuple(self._subscribers):
            callback = getattr(subscriber, method)
            task = asyncio.create_task(self._run_subscriber(subscriber, method, callback(event)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _run_subscriber(
        subscriber: ReactionSubscriber,
        method: str,
        coroutine: Coroutine[Any, Any, None],
    ) -> None:
        try:
            await coroutine
        except Exception:
            logger.exception("Reaction subscriber %r failed in %s", subscriber, method)


class ReactionRouterCog(commands.Cog):
    """Own the bot's only Discord raw-reaction listeners."""

    def __init__(self, bot: "squid.bot.app.RedstoneSquid") -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.bot.reactions.dispatch_add(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.bot.reactions.dispatch_remove(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self.bot.reactions.dispatch_clear(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self.bot.reactions.dispatch_clear_emoji(payload)

    @override
    async def cog_unload(self) -> None:
        await self.bot.reactions.close()


async def setup(bot: "squid.bot.app.RedstoneSquid") -> None:
    await bot.add_cog(ReactionRouterCog(bot))
