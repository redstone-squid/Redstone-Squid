import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from squid.bot.reactions import ReactionClearEvent, ReactionEvent, ReactionRouter


class Subscriber:
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

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        pass

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        pass


def payload(**overrides: Any) -> Any:
    values = {
        "message_id": 10,
        "channel_id": 20,
        "guild_id": 30,
        "user_id": 40,
        "emoji": "⭐",
        "member": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_router_isolates_subscribers_and_unregisters() -> None:
    bot = SimpleNamespace(get_guild=lambda guild_id: None)
    router = ReactionRouter(bot)  # type: ignore[arg-type]
    working = Subscriber()
    failing = Subscriber(fail=True)
    router.subscribe(working)
    router.subscribe(failing)

    await router.dispatch_add(payload())
    await router.close()

    assert len(working.events) == 1
    router.unsubscribe(working)
    await router.dispatch_add(payload())
    await router.close()
    assert len(working.events) == 1


async def test_router_shares_one_event_between_concurrent_subscribers() -> None:
    bot = SimpleNamespace(get_guild=lambda guild_id: None)
    router = ReactionRouter(bot)  # type: ignore[arg-type]
    first = Subscriber()
    second = Subscriber()
    router.subscribe(first)
    router.subscribe(second)

    await router.dispatch_add(payload())
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

    bot = SimpleNamespace(get_guild=lambda guild_id: Guild())
    event = ReactionEvent(payload(), "⭐", None, bot)  # type: ignore[arg-type]

    assert await asyncio.gather(event.resolve_member(), event.resolve_member()) == [member, member]
    assert calls == 1


async def test_event_resolves_message_once_without_untracking() -> None:
    message = object()
    fetch_message = AsyncMock(return_value=message)
    bot = SimpleNamespace(get_or_fetch_message=fetch_message)
    event = ReactionEvent(payload(), "⭐", None, bot)  # type: ignore[arg-type]

    assert await asyncio.gather(event.message(), event.message()) == [message, message]
    fetch_message.assert_awaited_once_with(20, 10, untrack_if_missing=False)
