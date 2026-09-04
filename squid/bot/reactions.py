"""Centralized Discord raw-reaction dispatch."""

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, override

import anyio
import discord
from discord.ext import commands

from squid.core.concurrency import run_all
from squid.observability import record_histogram
from squid.runtime import BackgroundTaskSupervisor, JobHandle

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
    """Dispatch reactions through bounded FIFO shards until :meth:`close` stops them.

    Calls admitted before shutdown wait for capacity rather than dropping a ballot. A dispatch
    is accepted once its item enters the queue; returning from ``dispatch_*`` confirms that
    acceptance. Events for one message share a shard and remain ordered, while different shards
    and the callbacks in one registration snapshot may run concurrently.
    """

    def __init__(
        self,
        bot: squid.bot.app.RedstoneSquid,
        supervisor: BackgroundTaskSupervisor,
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
        self._supervisor = supervisor
        self._registrations: dict[int, _ReactionRegistration] = {}
        self._next_registration_id = 0
        capacity = (max_pending + concurrency - 1) // concurrency
        self._queues = tuple(asyncio.Queue[_QueuedEvent](maxsize=capacity) for _ in range(concurrency))
        self._workers: tuple[JobHandle, ...] = ()
        self._intake_changed = asyncio.Condition()
        self._active_enqueues = 0
        self._outstanding = 0
        self._abort_enqueues = anyio.Event()
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
        """Stop intake, drain accepted events, then stop every owned shard worker."""
        async with self._intake_changed:
            if self._closing:
                return
            self._closing = True

        started = time.monotonic()
        with anyio.move_on_after(self._shutdown_timeout) as drain_scope:
            async with self._intake_changed:
                await self._intake_changed.wait_for(lambda: self._active_enqueues == 0)
            for queue in self._queues:
                await queue.join()

        if drain_scope.cancelled_caught:
            self._abort_enqueues.set()
            logger.error(
                "Reaction work did not drain before the shutdown deadline",
                extra={
                    "squid.reaction.active_enqueues": self._active_enqueues,
                    "squid.reaction.accepted_pending": self._outstanding,
                    "squid.reaction.shutdown_seconds": time.monotonic() - started,
                },
            )
            with anyio.move_on_after(1.0):
                async with self._intake_changed:
                    await self._intake_changed.wait_for(lambda: self._active_enqueues == 0)
        if self._workers:
            await self._supervisor.cancel(*self._workers)

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
        async with self._intake_changed:
            if self._closing:
                logger.warning("Ignored a reaction received during router shutdown")
                return
            self._active_enqueues += 1
        self._ensure_workers()
        try:
            queue = self._queues[item.event.payload.message_id % len(self._queues)]
            try:
                queue.put_nowait(item)
                accepted = True
            except asyncio.QueueFull:
                accepted = await self._enqueue_or_abort(queue, item)
            if accepted:
                self._outstanding += 1
        finally:
            async with self._intake_changed:
                self._active_enqueues -= 1
                self._intake_changed.notify_all()

    async def _enqueue_or_abort(self, queue: asyncio.Queue[_QueuedEvent], item: _QueuedEvent) -> bool:
        """Wait losslessly for queue capacity unless shutdown exhausts its drain budget."""
        finished = anyio.Event()
        accepted = False

        async def enqueue() -> None:
            nonlocal accepted
            await queue.put(item)
            accepted = True
            finished.set()

        async def abort() -> None:
            await self._abort_enqueues.wait()
            finished.set()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(enqueue)
            tasks.start_soon(abort)
            await finished.wait()
            tasks.cancel_scope.cancel()
        return accepted

    def _ensure_workers(self) -> None:
        if self._workers:
            return
        self._workers = tuple(
            self._supervisor.start(self._run_shard(queue), name=f"reaction-shard-{index}")
            for index, queue in enumerate(self._queues)
        )

    async def _run_shard(self, queue: asyncio.Queue[_QueuedEvent]) -> None:
        while True:
            item = await queue.get()
            try:
                record_histogram("squid.reaction.queue_latency", time.monotonic() - item.enqueued_at)
                await run_all(
                    functools.partial(self._run_callback, callback, item.kind, item.event)
                    for callback in item.callbacks
                )
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                # A shard that escapes this loop stops draining its queue for the
                # rest of the process, and close() then blocks on join() until the
                # shutdown deadline rather than reporting the real failure.
                logger.exception("Reaction shard failed to dispatch %s", item.kind)
            finally:
                self._outstanding -= 1
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
