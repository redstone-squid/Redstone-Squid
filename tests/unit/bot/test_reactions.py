"""Dispatch guarantees of the shared raw-reaction router."""

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast, override

import anyio
import discord
import pytest

import squid.bot.reactions as reactions_module
from squid.bot.reactions import ReactionEvent, ReactionResolver, ReactionRouter
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


@asynccontextmanager
async def running_router(
    *, concurrency: int = 16, max_pending: int = 1024, shutdown_timeout: float = 10
) -> AsyncIterator[ReactionRouter]:
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
        working_subscription = router.subscribe("working", add=working.on_reaction_add)
        router.subscribe("failing", add=failing.on_reaction_add)

        await router.dispatch_add(make_reaction_payload())
        working_subscription.detach()
        await router.dispatch_add(make_reaction_payload())
        await router.close()

    assert len(working.events) == 1


async def test_router_shares_one_event_between_concurrent_subscribers() -> None:
    async with running_router() as router:
        first = RecordingSubscriber()
        second = RecordingSubscriber()
        router.subscribe("first", add=first.on_reaction_add)
        router.subscribe("second", add=second.on_reaction_add)

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

    assert await run_all([event.resolve_member, event.resolve_member]) == [member, member]
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
        router.subscribe("ordered", add=subscriber.on_reaction_add, remove=subscriber.on_reaction_remove)
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
        router.subscribe("concurrent", add=subscriber.on_reaction_add)
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
        router.subscribe("slow", add=subscriber.on_reaction_add)
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
        router.subscribe("metrics-test", add=fail)
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
        {"squid.reaction.kind": "add", "squid.reaction.consumer": "metrics-test"},
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
        router.subscribe("hung", add=hang)
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
        router.subscribe("clear-consumer", clear=cleared, clear_emoji=cleared_emoji)
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
        router.subscribe("slow", add=slow)
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
        router.subscribe("vote", add=hang, recover_add=recover)
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
        router.subscribe("vote", add=handle, recover_add=recover)
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
        router.subscribe("vote", add=handle, recover_add=recover)
        await router.close()
        with caplog.at_level(logging.ERROR, logger="squid.bot.reactions"):
            await router.dispatch_add(make_reaction_payload(user_id=4))

    failed = next(record for record in caplog.records if "requires operator reconciliation" in record.getMessage())
    assert failed.__dict__["squid.reaction.consumer"] == "vote"
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
        router.subscribe("vote", add=hang, recover_add=recover)
        await router.dispatch_add(make_reaction_payload(user_id=1))
        await entered.wait()
        with caplog.at_level(logging.ERROR, logger="squid.bot.reactions"), anyio.fail_after(2):
            await router.close()

    assert any("recovery handoff did not finish" in record.getMessage() for record in caplog.records)
    deferred = next(record for record in caplog.records if "requires operator reconciliation" in record.getMessage())
    assert deferred.__dict__["squid.reaction.message_id"] == 10
    assert deferred.__dict__["squid.reaction.user_id"] == 1
    assert deferred.__dict__["squid.reaction.emoji"] == "⭐"
