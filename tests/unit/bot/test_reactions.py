"""Dispatch guarantees of the shared raw-reaction router."""

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast, override

import anyio
import discord
import pytest

import squid.bot.reactions as reactions_module
from squid.bot.reactions import (
    ReactionClearEvent,
    ReactionEvent,
    ReactionFilter,
    ReactionResolver,
    ReactionRouter,
    reaction_handler,
)
from squid.core.concurrency import run_all
from squid.runtime import BackgroundTaskSupervisor
from tests.support.discord import make_reaction_bot, make_reaction_payload


class RecordingSubscriber:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[ReactionEvent] = []

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        if self.fail:
            msg = "subscriber failed"
            raise RuntimeError(msg)
        self.events.append(event)

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        self.events.append(event)


def callback_subscriber(
    consumer: str,
    *,
    reaction_filter: ReactionFilter | None = None,
    add: Callable[[ReactionEvent], Awaitable[None]] | None = None,
    remove: Callable[[ReactionEvent], Awaitable[None]] | None = None,
    clear: Callable[[ReactionClearEvent], Awaitable[None]] | None = None,
    clear_emoji: Callable[[ReactionClearEvent], Awaitable[None]] | None = None,
    recover_add: Callable[[ReactionEvent], Awaitable[None]] | None = None,
) -> object:
    """Build a subscriber, logged as `consumer`, whose @reaction_handler methods forward to plain callbacks.

    Passing recover_add declares the add handler with recover=True and sends
    recovery calls there instead of to add.
    """
    namespace: dict[str, object] = {"reaction_filter": reaction_filter or ReactionFilter()}
    if add is not None or recover_add is not None:

        async def handle_add(_self: object, event: ReactionEvent) -> None:
            callback = recover_add if event.recovering else add
            assert callback is not None
            await callback(event)

        namespace["handle_add"] = reaction_handler("add", recover=recover_add is not None)(handle_add)
    if remove is not None:

        async def handle_remove(_self: object, event: ReactionEvent) -> None:
            await remove(event)

        namespace["handle_remove"] = reaction_handler("remove")(handle_remove)
    if clear is not None:

        async def handle_clear(_self: object, event: ReactionClearEvent) -> None:
            await clear(event)

        namespace["handle_clear"] = reaction_handler("clear")(handle_clear)
    if clear_emoji is not None:

        async def handle_clear_emoji(_self: object, event: ReactionClearEvent) -> None:
            await clear_emoji(event)

        namespace["handle_clear_emoji"] = reaction_handler("clear_emoji")(handle_clear_emoji)
    cls = type(consumer, (), namespace)
    cls.__qualname__ = consumer
    return cls()


@asynccontextmanager
async def running_router(
    *, concurrency: int = 16, max_pending: int = 1024, shutdown_timeout: float = 10
) -> AsyncIterator[ReactionRouter]:
    """Close the router before ending its supervisor, including when an assertion fails."""
    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(
            make_reaction_bot().bot,
            supervisor,
            concurrency=concurrency,
            max_pending=max_pending,
            shutdown_timeout=shutdown_timeout,
        )
        try:
            yield router
        finally:
            await router.close()


async def test_router_isolates_subscribers_and_unregisters() -> None:
    async with running_router() as router:
        working = RecordingSubscriber()
        failing = RecordingSubscriber(fail=True)
        working_subscription = router.subscribe(callback_subscriber("working", add=working.on_reaction_add))
        router.subscribe(callback_subscriber("failing", add=failing.on_reaction_add))

        await router.dispatch_add(make_reaction_payload())
        working_subscription.detach()
        await router.dispatch_add(make_reaction_payload())
        await router.close()

    assert len(working.events) == 1


@pytest.mark.parametrize(
    ("reaction_filter", "payload", "delivered"),
    [
        (ReactionFilter(), make_reaction_payload(user_id=40), True),
        (ReactionFilter(), make_reaction_payload(user_id=99), False),
        (ReactionFilter(include_own=True), make_reaction_payload(user_id=99), True),
        (ReactionFilter(emojis=frozenset({"❌"})), make_reaction_payload(emoji="⭐"), False),
        (ReactionFilter(emojis=frozenset({"❌", "⭐"})), make_reaction_payload(emoji="⭐"), True),
        (ReactionFilter(guild_only=True), make_reaction_payload(guild_id=None), False),
        (ReactionFilter(), make_reaction_payload(guild_id=None), True),
    ],
)
async def test_router_delivers_only_events_accepted_by_the_filter(
    reaction_filter: ReactionFilter, payload: discord.RawReactionActionEvent, delivered: bool
) -> None:
    # make_reaction_bot logs the bot in as user 99.
    async with running_router() as router:
        subscriber = RecordingSubscriber()
        router.subscribe(
            callback_subscriber(
                "filtered",
                reaction_filter=reaction_filter,
                add=subscriber.on_reaction_add,
                remove=subscriber.on_reaction_remove,
            )
        )

        await router.dispatch_add(payload)
        await router.close()

    assert bool(subscriber.events) is delivered


async def test_router_shares_one_event_between_concurrent_subscribers() -> None:
    async with running_router() as router:
        first = RecordingSubscriber()
        second = RecordingSubscriber()
        router.subscribe(callback_subscriber("first", add=first.on_reaction_add))
        router.subscribe(callback_subscriber("second", add=second.on_reaction_add))

        await router.dispatch_add(make_reaction_payload())
        await router.close()

    assert first.events[0] is second.events[0]


async def test_event_resolves_member_once() -> None:
    calls = 0
    member = object()

    class Guild:
        def get_member(self, user_id: int) -> None:
            return None

        async def fetch_member(self, user_id: int) -> object:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return member

    harness = make_reaction_bot(guild=cast(discord.Guild, Guild()))
    event = ReactionEvent(make_reaction_payload(), "⭐", ReactionResolver(harness.bot, None))

    assert await run_all([event.member, event.member]) == [member, member]
    assert calls == 1


async def test_event_resolves_message_once_without_untracking() -> None:
    message = cast(discord.Message, object())
    harness = make_reaction_bot(message=message)
    event = ReactionEvent(make_reaction_payload(), "⭐", ReactionResolver(harness.bot, None))

    assert await run_all([event.message, event.message]) == [message, message]
    harness.get_or_fetch_message.assert_awaited_once_with(20, 10)


async def test_add_then_remove_for_one_message_are_processed_in_order() -> None:
    async with running_router(concurrency=2, max_pending=2) as router:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        class OrderedSubscriber(RecordingSubscriber):
            @override
            async def on_reaction_add(self, event: ReactionEvent) -> None:
                calls.append("add-start")
                entered.set()
                await release.wait()
                calls.append("add-end")

            @override
            async def on_reaction_remove(self, event: ReactionEvent) -> None:
                calls.append("remove")

        subscriber = OrderedSubscriber()
        router.subscribe(
            callback_subscriber("ordered", add=subscriber.on_reaction_add, remove=subscriber.on_reaction_remove)
        )
        await router.dispatch_add(make_reaction_payload(message_id=10))
        await entered.wait()
        await router.dispatch_remove(make_reaction_payload(message_id=10, event_type="REACTION_REMOVE"))
        await asyncio.sleep(0)

        assert calls == ["add-start"]
        release.set()
        await router.close()
    assert calls == ["add-start", "add-end", "remove"]


async def test_different_message_shards_run_concurrently() -> None:
    async with running_router(concurrency=2, max_pending=2) as router:
        both_entered = asyncio.Event()
        release = asyncio.Event()
        entered: set[int] = set()

        class ConcurrentSubscriber(RecordingSubscriber):
            @override
            async def on_reaction_add(self, event: ReactionEvent) -> None:
                entered.add(event.payload.message_id)
                if len(entered) == 2:
                    both_entered.set()
                await release.wait()

        subscriber = ConcurrentSubscriber()
        router.subscribe(callback_subscriber("concurrent", add=subscriber.on_reaction_add))
        await router.dispatch_add(make_reaction_payload(message_id=10))
        await router.dispatch_add(make_reaction_payload(message_id=11))

        with anyio.fail_after(1):
            await both_entered.wait()
        release.set()
        await router.close()
    assert entered == {10, 11}


async def test_bounded_queue_applies_backpressure_without_losing_order() -> None:
    async with running_router(concurrency=1, max_pending=1) as router:
        entered = asyncio.Event()
        release = asyncio.Event()
        third_finished = anyio.Event()
        calls: list[int] = []

        class SlowSubscriber(RecordingSubscriber):
            @override
            async def on_reaction_add(self, event: ReactionEvent) -> None:
                calls.append(event.payload.user_id)
                if event.payload.user_id == 1:
                    entered.set()
                    await release.wait()

        async def dispatch_third() -> None:
            await router.dispatch_add(make_reaction_payload(user_id=3))
            third_finished.set()

        subscriber = SlowSubscriber()
        router.subscribe(callback_subscriber("slow", add=subscriber.on_reaction_add))
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        await router.dispatch_add(make_reaction_payload(user_id=2))
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch_third)
            await anyio.sleep(0)
            assert not third_finished.is_set()
            release.set()
            await third_finished.wait()
        await router.close()
    assert calls == [1, 2, 3]


def test_router_workers_use_structured_runtime_ownership() -> None:
    source = inspect.getsource(reactions_module)

    assert "asyncio.create_task" not in source
    assert "asyncio.gather" not in source
    assert "asyncio.timeout" not in source


async def test_router_records_enqueue_queue_handler_and_shutdown_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    histograms: list[tuple[str, Mapping[str, object]]] = []
    gauges: list[tuple[str, Mapping[str, object]]] = []
    counters: list[tuple[str, Mapping[str, object]]] = []

    def histogram(name: str, _value: float, *, attributes: Mapping[str, object] | None = None) -> None:
        histograms.append((name, attributes or {}))

    def gauge(name: str, _value: int | float, *, attributes: Mapping[str, object] | None = None) -> None:
        gauges.append((name, attributes or {}))

    def counter(name: str, *, attributes: Mapping[str, object] | None = None, value: int = 1) -> None:
        del value
        counters.append((name, attributes or {}))

    monkeypatch.setattr(reactions_module, "record_histogram", histogram)
    monkeypatch.setattr(reactions_module, "record_gauge", gauge)
    monkeypatch.setattr(reactions_module, "add_counter", counter)

    async def fail(_event: ReactionEvent) -> None:
        msg = "broken consumer"
        raise RuntimeError(msg)

    async with running_router() as router:
        router.subscribe(callback_subscriber("metrics-test", add=fail))
        await router.dispatch_add(make_reaction_payload())
        await router.close()

    histogram_names = {name for name, _attributes in histograms}
    assert {
        "squid.reaction.enqueue.wait",
        "squid.reaction.queue_latency",
        "squid.reaction.handler.duration",
        "squid.reaction.shutdown.drain_duration",
    } <= histogram_names
    assert {name for name, _attributes in gauges} >= {
        "squid.reaction.queue.depth",
        "squid.reaction.shutdown.accepted_pending",
    }
    assert (
        "squid.reaction.handler.failures",
        {"squid.reaction.kind": "add", "squid.reaction.consumer": "metrics-test.handle_add"},
    ) in counters


async def test_shutdown_aborts_blocked_intake_and_reports_accepted_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entered = anyio.Event()
    third_finished = anyio.Event()

    async def hang(_event: ReactionEvent) -> None:
        entered.set()
        await anyio.sleep_forever()

    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(
            make_reaction_bot().bot,
            supervisor,
            concurrency=1,
            max_pending=1,
            shutdown_timeout=0.01,
        )
        router.subscribe(callback_subscriber("hung", add=hang))
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        await router.dispatch_add(make_reaction_payload(user_id=2))

        async def dispatch_third() -> None:
            await router.dispatch_add(make_reaction_payload(user_id=3))
            third_finished.set()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch_third)
            await anyio.sleep(0)
            with caplog.at_level(logging.ERROR, logger="squid.bot.reactions"):
                await router.close()
            await third_finished.wait()

    timeout = next(record for record in caplog.records if "did not drain" in record.getMessage())
    assert timeout.__dict__["squid.reaction.active_enqueues"] == 1
    assert timeout.__dict__["squid.reaction.accepted_pending"] == 2


async def test_an_event_without_lookup_consumers_performs_no_discord_io() -> None:
    harness = make_reaction_bot(message=cast(discord.Message, object()))
    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(harness.bot, supervisor)
        await router.dispatch_add(make_reaction_payload())
        await router.close()

    harness.get_or_fetch_message.assert_not_awaited()


async def test_clear_and_clear_emoji_dispatch_only_their_typed_callbacks() -> None:
    calls: list[tuple[str, str | None]] = []

    async def cleared(event: reactions_module.ReactionClearEvent) -> None:
        calls.append(("clear", event.emoji))

    async def cleared_emoji(event: reactions_module.ReactionClearEvent) -> None:
        calls.append(("clear_emoji", event.emoji))

    async with running_router() as router:
        router.subscribe(callback_subscriber("clear-consumer", clear=cleared, clear_emoji=cleared_emoji))
        await router.dispatch_clear(cast(discord.RawReactionClearEvent, SimpleNamespace(message_id=10)))
        await router.dispatch_clear_emoji(
            cast(discord.RawReactionClearEmojiEvent, SimpleNamespace(message_id=10, emoji="⭐"))
        )
        await router.close()

    assert calls == [("clear", None), ("clear_emoji", "⭐")]


async def test_cancelling_a_blocked_enqueue_does_not_admit_it_later() -> None:
    entered = anyio.Event()
    release = anyio.Event()
    calls: list[int] = []

    async def slow(event: ReactionEvent) -> None:
        calls.append(event.payload.user_id)
        if event.payload.user_id == 1:
            entered.set()
            await release.wait()

    async with running_router(concurrency=1, max_pending=1) as router:
        router.subscribe(callback_subscriber("slow", add=slow))
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        await router.dispatch_add(make_reaction_payload(user_id=2))

        with anyio.move_on_after(0.01) as cancelled:
            await router.dispatch_add(make_reaction_payload(user_id=3))
        assert cancelled.cancelled_caught

        release.set()
        await router.close()

    assert calls == [1, 2]


async def test_shutdown_routes_an_unadmitted_vote_event_to_consumer_recovery() -> None:
    entered = anyio.Event()
    recovered: list[int] = []

    async def hang(event: ReactionEvent) -> None:
        if event.payload.user_id == 1:
            entered.set()
            await anyio.sleep_forever()

    async def recover(event: ReactionEvent) -> None:
        recovered.append(event.payload.user_id)

    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(
            make_reaction_bot().bot,
            supervisor,
            concurrency=1,
            max_pending=1,
            shutdown_timeout=0.01,
        )
        router.subscribe(callback_subscriber("vote", add=hang, recover_add=recover))
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        await router.dispatch_add(make_reaction_payload(user_id=2))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(router.dispatch_add, make_reaction_payload(user_id=3))
            await anyio.sleep(0)
            await router.close()

    assert sorted(recovered) == [1, 2, 3]


async def test_reaction_arriving_after_close_uses_bounded_recovery() -> None:
    recovered: list[int] = []

    async def handle(_event: ReactionEvent) -> None:
        pytest.fail("closed intake must not call the ordinary handler")

    async def recover(event: ReactionEvent) -> None:
        recovered.append(event.payload.user_id)

    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(make_reaction_bot().bot, supervisor)
        router.subscribe(callback_subscriber("vote", add=handle, recover_add=recover))
        await router.close()
        await router.dispatch_add(make_reaction_payload(user_id=4))

    assert recovered == [4]


async def test_failed_recovery_logs_the_complete_replay_identity(caplog: pytest.LogCaptureFixture) -> None:
    async def handle(_event: ReactionEvent) -> None:
        pytest.fail("closed intake must not call the ordinary handler")

    async def recover(_event: ReactionEvent) -> None:
        msg = "database unavailable"
        raise RuntimeError(msg)

    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(make_reaction_bot().bot, supervisor)
        router.subscribe(callback_subscriber("vote", add=handle, recover_add=recover))
        await router.close()
        with caplog.at_level(logging.ERROR, logger="squid.bot.reactions"):
            await router.dispatch_add(make_reaction_payload(user_id=4))

    failed = next(record for record in caplog.records if "requires operator reconciliation" in record.getMessage())
    assert failed.__dict__["squid.reaction.consumer"] == "vote.handle_add"
    assert failed.__dict__["squid.reaction.message_id"] == 10
    assert failed.__dict__["squid.reaction.channel_id"] == 20
    assert failed.__dict__["squid.reaction.guild_id"] == 30
    assert failed.__dict__["squid.reaction.user_id"] == 4
    assert failed.__dict__["squid.reaction.emoji"] == "⭐"


async def test_close_bounds_a_hanging_recovery_handoff(caplog: pytest.LogCaptureFixture) -> None:
    entered = anyio.Event()

    async def hang(event: ReactionEvent) -> None:
        if event.payload.user_id == 1:
            entered.set()
            await anyio.sleep_forever()

    async def recover(_event: ReactionEvent) -> None:
        await anyio.sleep_forever()

    async with BackgroundTaskSupervisor().running() as supervisor:
        router = ReactionRouter(
            make_reaction_bot().bot,
            supervisor,
            concurrency=1,
            max_pending=1,
            shutdown_timeout=0.01,
        )
        router.subscribe(callback_subscriber("vote", add=hang, recover_add=recover))
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        with caplog.at_level(logging.ERROR, logger="squid.bot.reactions"), anyio.fail_after(2):
            await router.close()

    assert any("recovery handoff did not finish" in record.getMessage() for record in caplog.records)
    deferred = next(record for record in caplog.records if "requires operator reconciliation" in record.getMessage())
    assert deferred.__dict__["squid.reaction.message_id"] == 10
    assert deferred.__dict__["squid.reaction.user_id"] == 1
    assert deferred.__dict__["squid.reaction.emoji"] == "⭐"


@pytest.mark.parametrize(("reactor_id", "expected"), [(99, True), (40, False)])
async def test_event_recognizes_own_reaction(reactor_id: int, expected: bool) -> None:
    harness = make_reaction_bot(user_id=99)
    event = ReactionEvent(make_reaction_payload(user_id=reactor_id), "⭐", ReactionResolver(harness.bot, None))

    assert event.is_own_reaction is expected


@pytest.mark.parametrize(
    ("author_id", "own", "accepted"),
    [(99, None, True), (40, None, True), (99, True, True), (40, True, False), (99, False, False), (40, False, True)],
)
async def test_own_message_filter_does_not_change_shared_lookup(
    author_id: int, own: bool | None, accepted: bool
) -> None:
    message = cast(discord.Message, SimpleNamespace(author=SimpleNamespace(id=author_id)))
    harness = make_reaction_bot(message=message, user_id=99)
    event = ReactionEvent(make_reaction_payload(), "⭐", ReactionResolver(harness.bot, None))

    assert await event.message(own=own) is (message if accepted else None)
    assert await event.message() is message
    harness.get_or_fetch_message.assert_awaited_once_with(20, 10)


@pytest.mark.parametrize("own", [True, False])
async def test_own_message_filter_handles_missing_message(own: bool) -> None:
    harness = make_reaction_bot()
    event = ReactionEvent(make_reaction_payload(), "⭐", ReactionResolver(harness.bot, None))

    assert await event.message(own=own) is None


@pytest.mark.parametrize("is_bot", [True, False, None])
async def test_humans_only_filter_does_not_change_shared_lookup(is_bot: bool | None) -> None:
    member = None if is_bot is None else cast(discord.Member, SimpleNamespace(bot=is_bot))
    harness = make_reaction_bot()
    event = ReactionEvent(make_reaction_payload(), "⭐", ReactionResolver(harness.bot, member))

    assert await event.member(humans_only=True) is (None if is_bot else member)
    assert await event.member() is member


class DecoratedSubscriber:
    reaction_filter = ReactionFilter(guild_only=True)

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    @reaction_handler("add", "remove", recover=True)
    async def record(self, event: ReactionEvent) -> None:
        self.calls.append((event.payload.event_type, event.recovering))

    @reaction_handler("clear", "clear_emoji")
    async def cleared(self, event: ReactionClearEvent) -> None:
        self.calls.append(("clear", event.recovering))


async def test_subscribe_routes_to_decorated_methods_with_the_class_filter() -> None:
    subscriber = DecoratedSubscriber()
    async with running_router() as router:
        router.subscribe(subscriber)
        await router.dispatch_add(make_reaction_payload())
        await router.dispatch_add(make_reaction_payload(guild_id=None))
        await router.dispatch_remove(make_reaction_payload(event_type="REACTION_REMOVE"))
        await router.dispatch_clear(cast(discord.RawReactionClearEvent, SimpleNamespace(message_id=10)))
        await router.close()
        # Intake is closed, so this add goes straight to recovery.
        await router.dispatch_add(make_reaction_payload())

    assert subscriber.calls == [
        ("REACTION_ADD", False),
        ("REACTION_REMOVE", False),
        ("clear", False),
        ("REACTION_ADD", True),
    ]


async def test_handlers_for_one_kind_run_concurrently_and_fail_independently() -> None:
    both_entered = anyio.Event()
    entered: list[str] = []

    class TwoHandlers:
        async def _enter(self, name: str) -> None:
            entered.append(name)
            if len(entered) == 2:
                both_entered.set()
            # Each handler waits for the other, so this only finishes if they run concurrently.
            with anyio.fail_after(1):
                await both_entered.wait()

        @reaction_handler("add")
        async def first(self, event: ReactionEvent) -> None:
            await self._enter("first")
            msg = "first handler failed"
            raise RuntimeError(msg)

        @reaction_handler("add")
        async def second(self, event: ReactionEvent) -> None:
            await self._enter("second")
            entered.append("second finished")

    async with running_router() as router:
        router.subscribe(TwoHandlers())
        await router.dispatch_add(make_reaction_payload())
        await router.close()

    assert sorted(entered) == ["first", "second", "second finished"]


async def test_subscribe_rejects_a_subscriber_without_handlers() -> None:
    async with running_router() as router:
        with pytest.raises(ValueError, match="no @reaction_handler"):
            router.subscribe(object())


def test_reaction_handler_rejects_mixed_event_types() -> None:
    with pytest.raises(TypeError, match="cannot mix"):
        reaction_handler("add", "clear")  # type: ignore[call-overload]
