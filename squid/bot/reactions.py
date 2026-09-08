"""Centralized Discord raw-reaction dispatch."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, override

import discord
from discord.ext import commands

from squid.observability import record_histogram

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    """One add or remove reaction, shared by every subscriber; `message()` and `resolve_member()` fetch once.

    `member` is only pre-filled when Discord or the guild cache supplies it, which for remove events is
    usually not the case.
    """

    payload: discord.RawReactionActionEvent
    emoji: str
    member: discord.Member | None
    _bot: squid.bot.app.RedstoneSquid = field(repr=False, compare=False)
    _message_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _message: discord.Message | None = field(default=None, init=False, repr=False, compare=False)
    _message_loaded: bool = field(default=False, init=False, repr=False, compare=False)
    _member_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _member_loaded: bool = field(default=False, init=False, repr=False, compare=False)

    async def message(self) -> discord.Message | None:
        """The reacted-to message, fetched at most once; None if it is gone or inaccessible."""
        async with self._message_lock:
            if self._message_loaded:
                return self._message
            message = await self._bot.get_or_fetch_message(self.payload.channel_id, self.payload.message_id)
            object.__setattr__(self, "_message", message)
            object.__setattr__(self, "_message_loaded", True)
            return message

    async def resolve_member(self) -> discord.Member | None:
        """The reacting member via cache then API, resolved at most once; None outside a guild or if not found."""
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
    """A reaction clear on one message; `emoji` is None when every reaction was cleared."""

    payload: discord.RawReactionClearEvent | discord.RawReactionClearEmojiEvent
    emoji: str | None = None


class ReactionSubscriber(Protocol):
    """Receives raw-reaction events from `ReactionRouter`.

    Every subscriber receives every event, concurrently with the others; an exception is logged and
    does not affect them. Events for one message arrive in gateway order. Register with
    `ReactionRouter.subscribe` and unregister on unload.
    """

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        """A reaction was added, including by the bot itself."""
        ...

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """A reaction was removed; `event.member` is usually None until `resolve_member()`."""
        ...

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """All reactions on a message were cleared; `event.emoji` is None."""
        ...

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        """Every reaction of one emoji was cleared; `event.emoji` names it."""
        ...


type ReactionMethod = Literal[
    "on_reaction_add",
    "on_reaction_remove",
    "on_reaction_clear",
    "on_reaction_clear_emoji",
]


@dataclass(frozen=True, slots=True)
class _QueuedReaction:
    method: ReactionMethod
    event: ReactionEvent | ReactionClearEvent
    subscribers: tuple[ReactionSubscriber, ...]
    enqueued_at: float


class ReactionRouter:
    """Fans raw reactions out through `concurrency` FIFO shards keyed by message id; `close` drains, then stops them.

    A full shard blocks the gateway listener rather than dropping the event; events arriving after
    `close` starts are dropped with a warning. The subscriber set is snapshotted at enqueue time.

    Raises:
        ValueError: `concurrency` is below 1 or `max_pending` is below `concurrency`.
    """

    def __init__(
        self,
        bot: squid.bot.app.RedstoneSquid,
        *,
        concurrency: int = 16,
        max_pending: int = 1024,
        shutdown_timeout: float = 10,
    ) -> None:
        if concurrency < 1:
            msg = "Reaction concurrency must be positive."
            raise ValueError(msg)
        if max_pending < concurrency:
            msg = "Reaction queue capacity must be at least the worker concurrency."
            raise ValueError(msg)
        self._bot = bot
        self._subscribers: set[ReactionSubscriber] = set()
        capacity = (max_pending + concurrency - 1) // concurrency
        self._queues = tuple(asyncio.Queue[_QueuedReaction](maxsize=capacity) for _ in range(concurrency))
        self._workers: tuple[asyncio.Task[None], ...] = ()
        self._producers: set[asyncio.Task[object]] = set()
        self._shutdown_timeout = shutdown_timeout
        self._closing = False

    def subscribe(self, subscriber: ReactionSubscriber) -> None:
        self._subscribers.add(subscriber)

    def unsubscribe(self, subscriber: ReactionSubscriber) -> None:
        self._subscribers.discard(subscriber)

    async def dispatch_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("on_reaction_add", payload)

    async def dispatch_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("on_reaction_remove", payload)

    async def dispatch_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self._dispatch("on_reaction_clear", ReactionClearEvent(payload))

    async def dispatch_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self._dispatch("on_reaction_clear_emoji", ReactionClearEvent(payload, str(payload.emoji)))

    async def close(self) -> None:
        """Idempotent; a drain that outlasts `shutdown_timeout` is logged and the workers are cancelled anyway."""
        if self._closing:
            return
        self._closing = True
        current = asyncio.current_task()
        producers = tuple(task for task in self._producers if task is not current)
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                if producers:
                    await asyncio.gather(*producers, return_exceptions=True)
                await asyncio.gather(*(queue.join() for queue in self._queues))
        except TimeoutError:
            logger.exception("Reaction work did not drain before the shutdown deadline")
        finally:
            for worker in self._workers:
                worker.cancel()
            if self._workers:
                await asyncio.gather(*self._workers, return_exceptions=True)

    async def _dispatch_action(self, method: ReactionMethod, payload: discord.RawReactionActionEvent) -> None:
        member = payload.member
        if member is None and payload.guild_id is not None:
            guild = self._bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id) if guild is not None else None
        await self._dispatch(method, ReactionEvent(payload, str(payload.emoji), member, self._bot))

    async def _dispatch(self, method: ReactionMethod, event: ReactionEvent | ReactionClearEvent) -> None:
        if self._closing:
            logger.warning("Ignored a reaction received during router shutdown")
            return
        self._ensure_workers()
        producer = asyncio.current_task()
        if producer is not None:
            self._producers.add(producer)
        try:
            item = _QueuedReaction(method, event, tuple(self._subscribers), time.monotonic())
            await self._queues[event.payload.message_id % len(self._queues)].put(item)
        finally:
            if producer is not None:
                self._producers.discard(producer)

    def _ensure_workers(self) -> None:
        if self._workers:
            return
        self._workers = tuple(
            asyncio.create_task(self._run_shard(queue), name=f"reaction-shard-{index}")
            for index, queue in enumerate(self._queues)
        )

    async def _run_shard(self, queue: asyncio.Queue[_QueuedReaction]) -> None:
        while True:
            item = await queue.get()
            try:
                record_histogram("squid.reaction.queue_latency", time.monotonic() - item.enqueued_at)
                await asyncio.gather(
                    *(self._run_subscriber(subscriber, item.method, item.event) for subscriber in item.subscribers)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A shard that escapes this loop stops draining its queue for the rest of the process,
                # and close() then blocks on join() until the deadline instead of reporting the failure.
                logger.exception("Reaction shard failed to dispatch %s", item.method)
            finally:
                queue.task_done()

    @staticmethod
    async def _run_subscriber(
        subscriber: ReactionSubscriber,
        method: ReactionMethod,
        event: ReactionEvent | ReactionClearEvent,
    ) -> None:
        try:
            callback = getattr(subscriber, method)
            await callback(event)
        except Exception:
            logger.exception("Reaction subscriber %r failed in %s", subscriber, method)


class ReactionRouterCog(commands.Cog):
    """The bot's only raw-reaction listeners; everything else subscribes to `bot.reactions`. Unloading closes the router."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
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


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(ReactionRouterCog(bot))
