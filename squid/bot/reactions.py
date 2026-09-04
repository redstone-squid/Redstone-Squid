"""Centralized Discord raw-reaction dispatch."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, override

import discord
from discord.ext import commands

from squid.observability import record_histogram

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    """A normalized reaction action with memoized Discord lookups."""

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
        """Fetch the reacted-to message at most once across all subscribers."""
        async with self._message_lock:
            if self._message_loaded:
                return self._message
            message = await self._bot.get_or_fetch_message(self.payload.channel_id, self.payload.message_id)
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


type ReactionActionCallback = Callable[[ReactionEvent], Awaitable[None]]
type ReactionClearCallback = Callable[[ReactionClearEvent], Awaitable[None]]
type ReactionKind = Literal["add", "remove", "clear", "clear_emoji"]


@dataclass(frozen=True, slots=True)
class _ReactionRegistration:
    consumer: str
    add: ReactionActionCallback | None
    remove: ReactionActionCallback | None
    clear: ReactionClearCallback | None
    clear_emoji: ReactionClearCallback | None


@dataclass(frozen=True, slots=True)
class ReactionSubscription:
    """A router registration that ends when :meth:`detach` is called."""

    _router: ReactionRouter = field(repr=False)
    _registration_id: int

    def detach(self) -> None:
        """Stop routing future events to this registration."""
        self._router._detach(self._registration_id)


@dataclass(frozen=True, slots=True)
class _ReactionCallback[EventT]:
    consumer: str
    callback: Callable[[EventT], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _QueuedReaction[EventT]:
    kind: ReactionKind
    event: EventT
    callbacks: tuple[_ReactionCallback[EventT], ...]
    enqueued_at: float


type _QueuedEvent = _QueuedReaction[ReactionEvent] | _QueuedReaction[ReactionClearEvent]


class ReactionRouter:
    """Dispatch reactions through bounded message-keyed FIFO shards."""

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
        self._registrations: dict[int, _ReactionRegistration] = {}
        self._next_registration_id = 0
        capacity = (max_pending + concurrency - 1) // concurrency
        self._queues = tuple(asyncio.Queue[_QueuedEvent](maxsize=capacity) for _ in range(concurrency))
        self._workers: tuple[asyncio.Task[None], ...] = ()
        self._producers: set[asyncio.Task[object]] = set()
        self._shutdown_timeout = shutdown_timeout
        self._closing = False

    def subscribe(
        self,
        consumer: str,
        *,
        add: ReactionActionCallback | None = None,
        remove: ReactionActionCallback | None = None,
        clear: ReactionClearCallback | None = None,
        clear_emoji: ReactionClearCallback | None = None,
    ) -> ReactionSubscription:
        """Register only the reaction callbacks one consumer implements.

        Events use a snapshot of registrations taken before enqueue, so detaching does not
        retract work the router has already accepted.
        """
        if all(callback is None for callback in (add, remove, clear, clear_emoji)):
            msg = "A reaction subscription requires at least one callback."
            raise ValueError(msg)
        registration_id = self._next_registration_id
        self._next_registration_id += 1
        self._registrations[registration_id] = _ReactionRegistration(consumer, add, remove, clear, clear_emoji)
        return ReactionSubscription(self, registration_id)

    def _detach(self, registration_id: int) -> None:
        self._registrations.pop(registration_id, None)

    async def dispatch_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("add", payload)

    async def dispatch_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("remove", payload)

    async def dispatch_clear(self, payload: discord.RawReactionClearEvent) -> None:
        callbacks = self._clear_callbacks("clear")
        await self._dispatch(_QueuedReaction("clear", ReactionClearEvent(payload), callbacks, time.monotonic()))

    async def dispatch_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        callbacks = self._clear_callbacks("clear_emoji")
        event = ReactionClearEvent(payload, str(payload.emoji))
        await self._dispatch(_QueuedReaction("clear_emoji", event, callbacks, time.monotonic()))

    async def close(self) -> None:
        """Stop intake, drain accepted events, then await every shard worker."""
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

    async def _dispatch_action(self, kind: Literal["add", "remove"], payload: discord.RawReactionActionEvent) -> None:
        member = payload.member
        if member is None and payload.guild_id is not None:
            guild = self._bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id) if guild is not None else None
        event = ReactionEvent(payload, str(payload.emoji), member, self._bot)
        await self._dispatch(_QueuedReaction(kind, event, self._action_callbacks(kind), time.monotonic()))

    def _action_callbacks(self, kind: Literal["add", "remove"]) -> tuple[_ReactionCallback[ReactionEvent], ...]:
        return tuple(
            _ReactionCallback(registration.consumer, callback)
            for registration in self._registrations.values()
            if (callback := registration.add if kind == "add" else registration.remove) is not None
        )

    def _clear_callbacks(
        self, kind: Literal["clear", "clear_emoji"]
    ) -> tuple[_ReactionCallback[ReactionClearEvent], ...]:
        return tuple(
            _ReactionCallback(registration.consumer, callback)
            for registration in self._registrations.values()
            if (callback := registration.clear if kind == "clear" else registration.clear_emoji) is not None
        )

    async def _dispatch(self, item: _QueuedEvent) -> None:
        if self._closing:
            logger.warning("Ignored a reaction received during router shutdown")
            return
        self._ensure_workers()
        producer = asyncio.current_task()
        if producer is not None:
            self._producers.add(producer)
        try:
            await self._queues[item.event.payload.message_id % len(self._queues)].put(item)
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

    async def _run_shard(self, queue: asyncio.Queue[_QueuedEvent]) -> None:
        while True:
            item = await queue.get()
            try:
                record_histogram("squid.reaction.queue_latency", time.monotonic() - item.enqueued_at)
                await asyncio.gather(
                    *(self._run_callback(callback, item.kind, item.event) for callback in item.callbacks)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A shard that escapes this loop stops draining its queue for the
                # rest of the process, and close() then blocks on join() until the
                # shutdown deadline rather than reporting the real failure.
                logger.exception("Reaction shard failed to dispatch %s", item.kind)
            finally:
                queue.task_done()

    @staticmethod
    async def _run_callback[EventT](
        callback: _ReactionCallback[EventT],
        kind: ReactionKind,
        event: EventT,
    ) -> None:
        try:
            await callback.callback(event)
        except Exception:
            logger.exception("Reaction consumer %s failed in %s", callback.consumer, kind)


class ReactionRouterCog(commands.Cog):
    """Own the bot's only Discord raw-reaction listeners."""

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
