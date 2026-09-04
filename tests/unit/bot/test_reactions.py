"""Dispatch guarantees of the shared raw-reaction router."""

import asyncio
from typing import cast, override

import discord

from squid.bot.reactions import ReactionEvent, ReactionRouter
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


async def test_router_isolates_subscribers_and_unregisters() -> None:
    router = ReactionRouter(make_reaction_bot().bot)
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
    router = ReactionRouter(make_reaction_bot().bot)
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

    assert await asyncio.gather(event.resolve_member(), event.resolve_member()) == [member, member]
    assert calls == 1


async def test_event_resolves_message_once_without_untracking() -> None:
    message = cast(discord.Message, object())
    harness = make_reaction_bot(message=message)
    event = ReactionEvent(make_reaction_payload(), "⭐", None, harness.bot)

    assert await asyncio.gather(event.message(), event.message()) == [message, message]
    harness.get_or_fetch_message.assert_awaited_once_with(20, 10)


async def test_add_then_remove_for_one_message_are_processed_in_order() -> None:
    router = ReactionRouter(make_reaction_bot().bot, concurrency=2, max_pending=2)
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
    router = ReactionRouter(make_reaction_bot().bot, concurrency=2, max_pending=2)
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

    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()
    await router.close()
    assert entered == {10, 11}


async def test_bounded_queue_applies_backpressure_without_losing_order() -> None:
    router = ReactionRouter(make_reaction_bot().bot, concurrency=1, max_pending=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    class SlowSubscriber(RecordingSubscriber):
        @override
        async def on_reaction_add(self, event: ReactionEvent) -> None:
            calls.append(event.payload.user_id)
            if event.payload.user_id == 1:
                entered.set()
                await release.wait()

    subscriber = SlowSubscriber()
    router.subscribe("slow", add=subscriber.on_reaction_add)
    await router.dispatch_add(make_reaction_payload(user_id=1))
    await entered.wait()
    await router.dispatch_add(make_reaction_payload(user_id=2))
    blocked = asyncio.create_task(router.dispatch_add(make_reaction_payload(user_id=3)))
    await asyncio.sleep(0)
    assert not blocked.done()

    release.set()
    await blocked
    await router.close()
    assert calls == [1, 2, 3]
