"""Centralized Discord raw-reaction dispatch."""

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast, override

import anyio
import discord
from discord.ext import commands

from squid.core.concurrency import run_all
from squid.observability import add_counter, record_gauge, record_histogram
from squid.runtime import BackgroundTaskSupervisor, JobHandle

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReactionResolver:
    """Discord lookups memoized for one routed event and its callback snapshot."""

    _bot: squid.bot.app.RedstoneSquid = field(repr=False, compare=False)
    _member: discord.Member | None = field(repr=False, compare=False)
    _message_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _message: discord.Message | None = field(default=None, init=False, repr=False, compare=False)
    _message_loaded: bool = field(default=False, init=False, repr=False, compare=False)
    _member_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _member_loaded: bool = field(default=False, init=False, repr=False, compare=False)

    async def message(self, channel_id: int, message_id: int) -> discord.Message | None:
        """Fetch the reacted-to message at most once across all subscribers."""
        async with self._message_lock:
            if self._message_loaded:
                return self._message
            message = await self._bot.get_or_fetch_message(channel_id, message_id)
            object.__setattr__(self, "_message", message)
            object.__setattr__(self, "_message_loaded", True)
            return message

    async def member(self, guild_id: int | None, user_id: int) -> discord.Member | None:
        """Resolve the reacting guild member at most once, only when a subscriber needs it."""
        async with self._member_lock:
            if self._member_loaded:
                return self._member
            member = self._member
            guild = self._bot.get_guild(guild_id) if guild_id is not None else None
            if member is None and guild is not None:
                member = guild.get_member(user_id)
            if member is None and guild is not None:
                with contextlib.suppress(discord.NotFound, discord.Forbidden):
                    member = await guild.fetch_member(user_id)
            object.__setattr__(self, "_member", member)
            object.__setattr__(self, "_member_loaded", True)
            return member


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    """A Discord reaction action with lookups scoped to this dispatch."""

    payload: discord.RawReactionActionEvent
    emoji: str
    _resolver: ReactionResolver = field(repr=False, compare=False)

    async def message(self) -> discord.Message | None:
        """Fetch the reacted-to message at most once across all callbacks."""
        return await self._resolver.message(self.payload.channel_id, self.payload.message_id)

    async def resolve_member(self) -> discord.Member | None:
        """Resolve the reacting member at most once across all callbacks."""
        return await self._resolver.member(self.payload.guild_id, self.payload.user_id)


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
    recover_add: ReactionActionCallback | None
    recover_remove: ReactionActionCallback | None
    recover_clear: ReactionClearCallback | None
    recover_clear_emoji: ReactionClearCallback | None


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


@dataclass(slots=True)
class _QueuedReaction[EventT]:
    kind: ReactionKind
    event: EventT
    callbacks: tuple[_ReactionCallback[EventT], ...]
    recoveries: tuple[_ReactionCallback[EventT], ...]
    created_at: float
    accepted_at: float | None = None


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
        self._accepted: dict[int, _QueuedEvent] = {}
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
        recover_add: ReactionActionCallback | None = None,
        recover_remove: ReactionActionCallback | None = None,
        recover_clear: ReactionClearCallback | None = None,
        recover_clear_emoji: ReactionClearCallback | None = None,
    ) -> ReactionSubscription:
        """Register only the reaction callbacks one consumer implements.

        Events use a snapshot of registrations taken before enqueue, so detaching does not
        retract work the router has already accepted. A ``recover_*`` callback is the
        consumer-owned reconciliation path for an event that cannot enter its shard before
        shutdown exhausts the drain deadline; it must either complete the state transition or
        record durable intent for a later retry.
        """
        if all(callback is None for callback in (add, remove, clear, clear_emoji)):
            msg = "A reaction subscription requires at least one callback."
            raise ValueError(msg)
        registration_id = self._next_registration_id
        self._next_registration_id += 1
        self._registrations[registration_id] = _ReactionRegistration(
            consumer,
            add,
            remove,
            clear,
            clear_emoji,
            recover_add,
            recover_remove,
            recover_clear,
            recover_clear_emoji,
        )
        return ReactionSubscription(self, registration_id)

    def _detach(self, registration_id: int) -> None:
        self._registrations.pop(registration_id, None)

    async def dispatch_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("add", payload)

    async def dispatch_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._dispatch_action("remove", payload)

    async def dispatch_clear(self, payload: discord.RawReactionClearEvent) -> None:
        callbacks = self._clear_callbacks("clear")
        recoveries = self._clear_callbacks("clear", recovery=True)
        await self._dispatch(
            _QueuedReaction("clear", ReactionClearEvent(payload), callbacks, recoveries, time.monotonic())
        )

    async def dispatch_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        callbacks = self._clear_callbacks("clear_emoji")
        recoveries = self._clear_callbacks("clear_emoji", recovery=True)
        event = ReactionClearEvent(payload, str(payload.emoji))
        await self._dispatch(_QueuedReaction("clear_emoji", event, callbacks, recoveries, time.monotonic()))

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

        pending_at_timeout: tuple[_QueuedEvent, ...] = ()
        if drain_scope.cancelled_caught:
            self._abort_enqueues.set()
            pending_at_timeout = tuple(self._accepted.values())
            add_counter("squid.reaction.shutdown.timeouts")
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
        shutdown_attributes = {"squid.outcome": "timeout" if drain_scope.cancelled_caught else "drained"}
        record_histogram(
            "squid.reaction.shutdown.drain_duration",
            time.monotonic() - started,
            attributes=shutdown_attributes,
        )
        record_gauge(
            "squid.reaction.shutdown.accepted_pending",
            self._outstanding,
            attributes=shutdown_attributes,
        )
        if self._workers:
            await self._supervisor.cancel(*self._workers)
        if pending_at_timeout:
            with anyio.move_on_after(1.0) as recovery_scope:
                # Reconciliation is idempotent but not safe to run concurrently for two
                # snapshots of the same toggling consumer. Preserve acceptance order here.
                for item in pending_at_timeout:
                    await self._recover_unadmitted(item)
            if recovery_scope.cancelled_caught:
                add_counter("squid.reaction.recovery.timeouts")
                logger.error(
                    "Reaction recovery handoff did not finish before shutdown",
                    extra={"squid.reaction.recovery.pending": len(pending_at_timeout)},
                )
                for item in pending_at_timeout:
                    self._log_deferred_recovery(item, reason="accepted_shutdown_timeout")

    async def _dispatch_action(self, kind: Literal["add", "remove"], payload: discord.RawReactionActionEvent) -> None:
        member = payload.member
        if member is None and payload.guild_id is not None:
            guild = self._bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id) if guild is not None else None
        event = ReactionEvent(payload, str(payload.emoji), ReactionResolver(self._bot, member))
        await self._dispatch(
            _QueuedReaction(
                kind,
                event,
                self._action_callbacks(kind),
                self._action_callbacks(kind, recovery=True),
                time.monotonic(),
            )
        )

    def _action_callbacks(
        self, kind: Literal["add", "remove"], *, recovery: bool = False
    ) -> tuple[_ReactionCallback[ReactionEvent], ...]:
        return tuple(
            _ReactionCallback(registration.consumer, callback)
            for registration in self._registrations.values()
            if (
                callback := (
                    registration.recover_add
                    if recovery and kind == "add"
                    else registration.recover_remove
                    if recovery
                    else registration.add
                    if kind == "add"
                    else registration.remove
                )
            )
            is not None
        )

    def _clear_callbacks(
        self, kind: Literal["clear", "clear_emoji"], *, recovery: bool = False
    ) -> tuple[_ReactionCallback[ReactionClearEvent], ...]:
        return tuple(
            _ReactionCallback(registration.consumer, callback)
            for registration in self._registrations.values()
            if (
                callback := (
                    registration.recover_clear
                    if recovery and kind == "clear"
                    else registration.recover_clear_emoji
                    if recovery
                    else registration.clear
                    if kind == "clear"
                    else registration.clear_emoji
                )
            )
            is not None
        )

    async def _dispatch(self, item: _QueuedEvent) -> None:
        closing = False
        async with self._intake_changed:
            if self._closing:
                closing = True
            else:
                self._active_enqueues += 1
        if closing:
            with anyio.move_on_after(0.5) as recovery_scope:
                await self._recover_unadmitted(item)
            outcome = "deferred" if recovery_scope.cancelled_caught else "recovered"
            add_counter(f"squid.reaction.intake.{outcome}", attributes={"squid.reaction.kind": item.kind})
            if recovery_scope.cancelled_caught:
                self._log_deferred_recovery(item, reason="closing_intake_timeout")
            return
        self._ensure_workers()
        try:
            shard = item.event.payload.message_id % len(self._queues)
            queue = self._queues[shard]
            try:
                queue.put_nowait(item)
                self._mark_accepted(item, queue, shard)
                accepted = True
            except asyncio.QueueFull:
                accepted = await self._enqueue_or_abort(queue, item, shard)
            if not accepted:
                add_counter("squid.reaction.enqueue.aborted", attributes={"squid.reaction.kind": item.kind})
                with anyio.move_on_after(0.5) as recovery_scope:
                    await self._recover_unadmitted(item)
                if recovery_scope.cancelled_caught:
                    add_counter("squid.reaction.recovery.timeouts")
                    self._log_deferred_recovery(item, reason="unadmitted_shutdown_timeout")
        finally:
            async with self._intake_changed:
                self._active_enqueues -= 1
                self._intake_changed.notify_all()

    def _mark_accepted(self, item: _QueuedEvent, queue: asyncio.Queue[_QueuedEvent], shard: int) -> None:
        """Account for an accepted item before its worker can observe it."""
        accepted_at = time.monotonic()
        item.accepted_at = accepted_at
        self._outstanding += 1
        self._accepted[id(item)] = item
        attributes = {"squid.reaction.kind": item.kind, "squid.reaction.shard": shard}
        record_histogram(
            "squid.reaction.enqueue.wait",
            accepted_at - item.created_at,
            attributes=attributes,
        )
        record_gauge("squid.reaction.queue.depth", queue.qsize(), attributes=attributes)

    async def _enqueue_or_abort(self, queue: asyncio.Queue[_QueuedEvent], item: _QueuedEvent, shard: int) -> bool:
        """Wait losslessly for queue capacity unless shutdown exhausts its drain budget."""
        finished = anyio.Event()
        accepted = False

        async def enqueue() -> None:
            nonlocal accepted
            await queue.put(item)
            self._mark_accepted(item, queue, shard)
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

    async def _recover_unadmitted(self, item: _QueuedEvent) -> None:
        """Hand a shutdown-aborted event to each consumer that declared recovery."""
        if not item.recoveries:
            return
        add_counter(
            "squid.reaction.recovery.attempts",
            attributes={"squid.reaction.kind": item.kind},
        )
        await run_all(
            functools.partial(self._run_recovery, callback, item.kind, item.event)
            for callback in item.recoveries
        )

    @staticmethod
    def _log_deferred_recovery(item: _QueuedEvent, *, reason: str) -> None:
        """Emit the immutable raw identifiers needed for operator replay."""
        attributes = ReactionRouter._recovery_attributes(item.kind, item.event, reason=reason)
        logger.error("Reaction intent requires operator reconciliation", extra=attributes)

    @staticmethod
    def _recovery_attributes(
        kind: ReactionKind,
        event: ReactionEvent | ReactionClearEvent,
        *,
        reason: str,
        consumer: str | None = None,
    ) -> dict[str, str | int]:
        """Build the replay envelope shared by timeout and callback-failure logs."""
        payload = event.payload
        attributes: dict[str, str | int] = {
            "squid.reaction.kind": kind,
            "squid.reaction.message_id": payload.message_id,
            "squid.reaction.channel_id": payload.channel_id,
            "squid.reaction.recovery_reason": reason,
        }
        if consumer is not None:
            attributes["squid.reaction.consumer"] = consumer
        if payload.guild_id is not None:
            attributes["squid.reaction.guild_id"] = payload.guild_id
        if isinstance(event, ReactionEvent):
            attributes["squid.reaction.user_id"] = event.payload.user_id
            attributes["squid.reaction.emoji"] = event.emoji
        elif event.emoji is not None:
            attributes["squid.reaction.emoji"] = event.emoji
        return attributes

    @staticmethod
    async def _run_recovery[EventT](
        callback: _ReactionCallback[EventT],
        kind: ReactionKind,
        event: EventT,
    ) -> None:
        attributes = {"squid.reaction.kind": kind, "squid.reaction.consumer": callback.consumer}
        try:
            await callback.callback(event)
        except Exception:
            add_counter("squid.reaction.recovery.failures", attributes=attributes)
            replay = ReactionRouter._recovery_attributes(
                kind,
                cast(ReactionEvent | ReactionClearEvent, event),
                reason="consumer_callback_failure",
                consumer=callback.consumer,
            )
            logger.exception(
                "Reaction consumer %s failed to recover an unadmitted %s event; intent requires operator reconciliation",
                callback.consumer,
                kind,
                extra=replay,
            )

    def _ensure_workers(self) -> None:
        if self._workers:
            return
        self._workers = tuple(
            self._supervisor.start(self._run_shard(index, queue), name=f"reaction-shard-{index}")
            for index, queue in enumerate(self._queues)
        )

    async def _run_shard(self, shard: int, queue: asyncio.Queue[_QueuedEvent]) -> None:
        while True:
            item = await queue.get()
            attributes = {"squid.reaction.kind": item.kind, "squid.reaction.shard": shard}
            try:
                accepted_at = item.accepted_at or item.created_at
                record_histogram("squid.reaction.queue_latency", time.monotonic() - accepted_at, attributes=attributes)
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
                add_counter("squid.reaction.shard.failures", attributes=attributes)
            finally:
                self._outstanding -= 1
                self._accepted.pop(id(item), None)
                queue.task_done()
                record_gauge("squid.reaction.queue.depth", queue.qsize(), attributes=attributes)

    @staticmethod
    async def _run_callback[EventT](
        callback: _ReactionCallback[EventT],
        kind: ReactionKind,
        event: EventT,
    ) -> None:
        started = time.monotonic()
        attributes = {"squid.reaction.kind": kind, "squid.reaction.consumer": callback.consumer}
        try:
            await callback.callback(event)
        except Exception:
            add_counter("squid.reaction.handler.failures", attributes=attributes)
            logger.exception(
                "Reaction consumer %s failed in %s",
                callback.consumer,
                kind,
                extra=dict(attributes),
            )
        finally:
            record_histogram("squid.reaction.handler.duration", time.monotonic() - started, attributes=attributes)


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
