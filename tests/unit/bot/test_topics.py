"""The bot's topic vocabulary and live-build publishing path."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio

import squid_layouts as sl
from squid.bot.app import RedstoneSquid
from squid.bot.topics import follow_resource
from squid.topics import resource_topic
from squid_layouts.discord import Everyone
from squid_layouts.discord.testing import delivered_to, fake_message


class Projection(sl.Component):
    def __init__(self, value: str) -> None:
        self.value = value

    def render(self):
        return sl.paragraph(self.value)


async def _drain_reactor(reactor: sl.discord.Reactor) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_one_resource_publish_refreshes_two_panels_without_second_post_writer() -> None:
    bus = sl.TopicBus()
    reactor = sl.discord.Reactor(bus)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    panels = [Projection("before"), Projection("before")]
    source = "before"

    for panel, message in zip(panels, messages, strict=True):
        mount = sl.discord.Mount(panel, access=Everyone(), scheduler=reactor, timeout=None)

        async def reload(current: Projection) -> None:
            current.value = source

        follow_resource(bus, reactor, mount, resource_topic("build", "42"), panel, reload)
        await mount.send(delivered_to(message))

    posts = SimpleNamespace(pending_generation=AsyncMock(return_value=7))
    reconciler = SimpleNamespace(reconcile=AsyncMock())
    bot = RedstoneSquid.__new__(RedstoneSquid)
    bot.topic_bus = bus
    bot.topic_publisher = bus
    bot.services = cast(Any, SimpleNamespace(posts=posts))
    bot.post_reconciler = cast(Any, reconciler)
    source = "after"

    await RedstoneSquid.refresh_posts(bot, "build", "42")
    await bus.drain()
    await _drain_reactor(reactor)

    assert all("after" in str(message.edit.await_args.kwargs["view"].to_components()) for message in messages)
    reconciler.reconcile.assert_awaited_once_with("build", "42", 7)
