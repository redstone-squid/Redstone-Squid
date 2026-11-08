"""Route Discord reaction events to voting, starboard, and other subscribers.

ReactionRouterCog receives the gateway events and hands them to ReactionRouter.
A subscriber is any object with methods marked by @reaction_handler; the bot
subscribes cogs automatically when they are added and detaches them when they
are removed, and other objects call ReactionRouter.subscribe themselves.

Subscribers share message and member lookups for each event. The router keeps
events for one message ordered; each subscriber decides what a reaction means
and supplies any recovery needed when shutdown interrupts its work.
"""

import asyncio
import contextlib
import dataclasses
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast, overload, override

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
        """Share one completed message lookup across subscribers, including a missing result.

        A raised lookup error is not memoized, so a later subscriber can try again.
        """
        async with self._message_lock:
            if self._message_loaded:
                return self._message
            message = await self._bot.get_or_fetch_message(channel_id, message_id)
            object.__setattr__(self, "_message", message)
            object.__setattr__(self, "_message_loaded", True)
            return message

    async def member(self, guild_id: int | None, user_id: int) -> discord.Member | None:
        """Reuse payload or cached membership, fetching only when needed.

        Missing membership is memoized; lookup errors that propagate are not.
        """
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

    @property
    def own_user_id(self) -> int:
        """Return this bot's Discord user ID; reactions only arrive after login sets it."""
        user = self._bot.user
        assert user is not None
        return user.id


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    """A reaction added or removed by a Discord user.

    Subscribers receive the same event and share its message and member lookups.
    The filters on message() and member() affect only the caller's result, never
    the shared lookup another subscriber sees.

    "Own" refers to this bot's Discord account: an own reaction is one the bot
    added, and an own message is one the bot sent.
    """

    payload: discord.RawReactionActionEvent
    emoji: str
    _resolver: ReactionResolver = field(repr=False, compare=False)
    recovering: bool = False
    """True when a handler declared with recover=True is called during shutdown recovery."""

    @property
    def is_own_reaction(self) -> bool:
        """Whether this bot added or removed the reaction."""
        return self.payload.user_id == self._resolver.own_user_id

    async def message(self, *, own: bool | None = None) -> discord.Message | None:
        """Return the message that received the reaction.

        The first call fetches the message; later calls on this event reuse that result.

        Args:
            own: True accepts only messages this bot sent, False only messages
                someone else sent, and None accepts any message.

        Returns:
            The message, or None if it is rejected by `own` or cannot be found or
            accessed. Other lookup errors propagate, and a later call retries.
        """
        message = await self._resolver.message(self.payload.channel_id, self.payload.message_id)
        if message is None or own is None:
            return message
        return message if (message.author.id == self._resolver.own_user_id) == own else None

    async def member(self, *, humans_only: bool = False) -> discord.Member | None:
        """Return the guild member who reacted, or None outside a guild or if they cannot be found.

        Args:
            humans_only: Return None for bot accounts.
        """
        member = await self._resolver.member(self.payload.guild_id, self.payload.user_id)
        if member is not None and humans_only and member.bot:
            return None
        return member


@dataclass(frozen=True, slots=True)
class ReactionFilter:
    """Choose which reaction add and remove events reach a subscriber.

    A subscriber declares one for all its handlers as a `reaction_filter`
    class attribute, and a handler can add its own with
    `@reaction_handler(filter=...)`; an event must pass both. The router checks
    filters against the gateway payload before queuing handlers, including
    recovery, so a rejected event costs no Discord lookups. Clear events are
    not filtered.

    The default filter accepts every reaction except this bot's own.
    """

    emojis: frozenset[str] | None = None
    """Accept only these emoji strings, compared exactly with ReactionEvent.emoji. None accepts any emoji."""
    guild_only: bool = False
    """Reject reactions in direct messages."""
    include_own: bool = False
    """Accept reactions this bot added or removed."""

    def accepts(self, event: ReactionEvent) -> bool:
        """Return whether `event` passes every check."""
        if self.emojis is not None and event.emoji not in self.emojis:
            return False
        if self.guild_only and event.payload.guild_id is None:
            return False
        return self.include_own or not event.is_own_reaction


_DEFAULT_FILTER = ReactionFilter()


@dataclass(frozen=True, slots=True)
class ReactionClearEvent:
    """A normalized clear event, optionally scoped to one emoji."""

    payload: discord.RawReactionClearEvent | discord.RawReactionClearEmojiEvent
    emoji: str | None = None
    recovering: bool = False
    """True when a handler declared with recover=True is called during shutdown recovery."""


type ReactionKind = Literal["add", "remove", "clear", "clear_emoji"]
type ReactionActionKind = Literal["add", "remove"]
type ReactionClearKind = Literal["clear", "clear_emoji"]

_HANDLER_ATTRIBUTE = "__squid_reaction_handler__"
_ACTION_KINDS: frozenset[str] = frozenset({"add", "remove"})


@dataclass(frozen=True, slots=True)
class _HandlerSpec:
    kinds: tuple[ReactionKind, ...]
    recover: bool
    reaction_filter: ReactionFilter | None


@overload
def reaction_handler[F: Callable[[Any, ReactionEvent], Awaitable[None]]](
    *kinds: ReactionActionKind, filter: ReactionFilter | None = None, recover: bool = False
) -> Callable[[F], F]: ...


@overload
def reaction_handler[F: Callable[[Any, ReactionClearEvent], Awaitable[None]]](
    *kinds: ReactionClearKind, recover: bool = False
) -> Callable[[F], F]: ...


def reaction_handler[F: Callable[..., Awaitable[None]]](
    *kinds: ReactionKind, filter: ReactionFilter | None = None, recover: bool = False
) -> Callable[[F], F]:
    """Mark a method to receive the given kinds of reaction event.

    "add" and "remove" handlers receive a ReactionEvent; "clear" and
    "clear_emoji" handlers receive a ReactionClearEvent, so one method cannot
    mix the two groups. A subscriber may have several handlers for one kind;
    they run concurrently, so steps that must happen in order belong in one
    handler.

    Args:
        kinds: The events this method handles.
        filter: Further restrict which add and remove events reach this
            handler. An event must pass both this filter and the subscriber's
            `reaction_filter`, so this cannot accept anything the subscriber's
            filter rejects. Clear events are not filtered, so clear handlers
            take no filter.
        recover: Also call this method for events that shutdown interrupted
            or never queued. The event's `recovering` flag is then True. A
            recovering handler must tolerate partial earlier processing.
    """
    if not kinds:
        msg = "reaction_handler needs at least one event kind."
        raise TypeError(msg)
    if len({kind in _ACTION_KINDS for kind in kinds}) > 1:
        msg = "reaction_handler cannot mix add/remove with clear events; they receive different event types."
        raise TypeError(msg)

    def decorate(method: F) -> F:
        setattr(method, _HANDLER_ATTRIBUTE, _HandlerSpec(kinds, recover, filter))
        return method

    return decorate


def _handler_specs(subscriber: object) -> list[tuple[str, _HandlerSpec]]:
    """Return the name and spec of every @reaction_handler method on the subscriber's class."""
    cls = type(subscriber)
    specs: list[tuple[str, _HandlerSpec]] = []
    for name in dir(cls):
        # getattr_static so scanning never runs a property or descriptor.
        spec = getattr(inspect.getattr_static(cls, name), _HANDLER_ATTRIBUTE, None)
        if isinstance(spec, _HandlerSpec):
            specs.append((name, spec))
    return specs


def has_reaction_handlers(subscriber: object) -> bool:
    """Return whether the subscriber's class has any @reaction_handler method."""
    return bool(_handler_specs(subscriber))


@dataclass(frozen=True, slots=True)
class _BoundHandler:
    """One @reaction_handler method bound to its subscriber."""

    consumer: str
    """The method's qualified name, e.g. "Admin.delete_archived_message", used in logs and metrics."""
    kinds: frozenset[ReactionKind]
    recover: bool
    filters: tuple[ReactionFilter, ...]
    """The subscriber's filter, then the handler's own if it has one. An event must pass all of them."""
    callback: Callable[[Any], Awaitable[None]]

    def accepts(self, event: ReactionEvent) -> bool:
        return all(reaction_filter.accepts(event) for reaction_filter in self.filters)


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
    """Dispatch reactions through bounded FIFO queues partitioned by message ID.

    Events for one message share a worker and preserve queue order. Subscribers
    for one event run concurrently, as do different queues. Ordinary dispatch
    waits for queue capacity and returns after acceptance, not callback completion.

    During shutdown, dispatch may instead attempt recovery and return without
    queueing. Recovery is bounded and may require operator reconciliation; normal
    callback failures are logged without automatic replay.
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
        self._registrations: dict[int, tuple[_BoundHandler, ...]] = {}
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

    def subscribe(self, subscriber: object) -> ReactionSubscription:
        """Route events to the subscriber's @reaction_handler methods until detached.

        The bot calls this for cogs; other subscribers call it when they start.
        The subscriber's `reaction_filter` class attribute, if any, selects which
        add and remove events reach its handlers; a handler's own `filter`
        restricts them further.

        Dispatch captures registrations before enqueueing, so detaching does not
        retract that snapshot. Recovery can run for events that never entered a queue
        or for accepted events interrupted during shutdown.

        Raises:
            ValueError: The subscriber has no @reaction_handler methods.
            TypeError: The subscriber's `reaction_filter` is not a ReactionFilter.
        """
        cls_name = type(subscriber).__qualname__
        reaction_filter = getattr(subscriber, "reaction_filter", _DEFAULT_FILTER)
        if not isinstance(reaction_filter, ReactionFilter):
            msg = f"{cls_name}.reaction_filter must be a ReactionFilter."
            raise TypeError(msg)
        handlers = tuple(
            _BoundHandler(
                f"{cls_name}.{name}",
                frozenset(spec.kinds),
                spec.recover,
                (reaction_filter,) if spec.reaction_filter is None else (reaction_filter, spec.reaction_filter),
                getattr(subscriber, name),
            )
            for name, spec in _handler_specs(subscriber)
        )
        if not handlers:
            msg = f"{cls_name} has no @reaction_handler methods."
            raise ValueError(msg)
        registration_id = self._next_registration_id
        self._next_registration_id += 1
        self._registrations[registration_id] = handlers
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
        """Stop intake, drain work within the deadline, then cancel workers.

        On timeout, abort pending enqueues and attempt bounded recovery for accepted
        unfinished events. A second call returns immediately, even if the first is
        still shutting down.
        """
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
                self._action_callbacks(kind, event),
                self._action_callbacks(kind, event, recovery=True),
                time.monotonic(),
            )
        )

    def _action_callbacks(
        self, kind: Literal["add", "remove"], event: ReactionEvent, *, recovery: bool = False
    ) -> tuple[_ReactionCallback[ReactionEvent], ...]:
        return tuple(
            _ReactionCallback(handler.consumer, handler.callback)
            for handler in self._handlers(kind, recovery=recovery)
            if handler.accepts(event)
        )

    def _clear_callbacks(
        self, kind: Literal["clear", "clear_emoji"], *, recovery: bool = False
    ) -> tuple[_ReactionCallback[ReactionClearEvent], ...]:
        return tuple(
            _ReactionCallback(handler.consumer, handler.callback) for handler in self._handlers(kind, recovery=recovery)
        )

    def _handlers(self, kind: ReactionKind, *, recovery: bool) -> Iterator[_BoundHandler]:
        for handlers in self._registrations.values():
            for handler in handlers:
                if kind in handler.kinds and (handler.recover or not recovery):
                    yield handler

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
        """Run declared recovery callbacks concurrently for an event interrupted by shutdown."""
        if not item.recoveries:
            return
        add_counter(
            "squid.reaction.recovery.attempts",
            attributes={"squid.reaction.kind": item.kind},
        )
        await run_all(
            # `_QueuedEvent` is a union of two `_QueuedReaction` instantiations, so the checker cannot
            # rebind EventT across the union even though each queued item pairs its own event type with
            # its own callbacks -- which is exactly what the generic dataclass guarantees.
            functools.partial(  # pyright: ignore[reportArgumentType]
                self._run_recovery, callback, item.kind, dataclasses.replace(item.event, recovering=True)
            )
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
                    # See `_recover_unadmitted`: the union of `_QueuedReaction` instantiations hides the
                    # event/callback pairing the dataclass guarantees.
                    functools.partial(self._run_callback, callback, item.kind, item.event)  # pyright: ignore[reportArgumentType]
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
