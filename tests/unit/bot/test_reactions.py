"""Dispatch guarantees of the shared raw-reaction router."""

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast, override

import anyio
import discord

import squid.bot.reactions as reactions_module
from squid.bot.reactions import ReactionEvent, ReactionRouter
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
    event = ReactionEvent(make_reaction_payload(), "⭐", None, harness.bot)

    assert await run_all([event.resolve_member, event.resolve_member]) == [member, member]
    assert calls == 1


async def test_event_resolves_message_once_without_untracking() -> None:
    message = cast(discord.Message, object())
    harness = make_reaction_bot(message=message)
    event = ReactionEvent(make_reaction_payload(), "⭐", None, harness.bot)

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
