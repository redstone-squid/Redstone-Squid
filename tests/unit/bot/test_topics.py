"""The bot's topic vocabulary and live-build publishing path."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio

import squid_ui_discord as sd
import squid_ui as sl
from squid.bot.app import RedstoneSquid
from squid.topics import resource_topic
from squid_ui_discord import Everyone
from squid_ui_discord.testing import delivered_to, fake_message


class Projection(sl.Component):
    """A panel that re-reads its source whenever the build topic is published.

    The whole binding is the `sl.runtime.watch` line: no subscription to register, no reload closure
    to pass, and no priming call before the send.
    """

    def __init__(self, read) -> None:
        self._read = read

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def value(self) -> str:
        sl.runtime.watch(resource_topic("build", "42"))
        return self._read()

    def render(self):
        # An atomic resource is still rendered once while pending: that discovery render is
        # how the mount learns the resource exists, and what it reads is what it follows.
        match self.value.status:
            case sl.resources.Ready(value=value):
                return sl.paragraph(value)
            case _:
                return sl.paragraph("loading")


async def _drain_scheduler(scheduler: sd.MountScheduler) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await asyncio.wait_for(scheduler._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_one_resource_publish_refreshes_two_panels_without_second_post_writer() -> None:
    bus = sl.runtime.LocalTopicBus()
    scheduler = sd.MountScheduler(bus)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    source = "before"
    panels = [Projection(lambda: source), Projection(lambda: source)]

    for panel, message in zip(panels, messages, strict=True):
        mount = sd.Mount(panel, access=Everyone(), scheduler=scheduler, timeout=None)
        await mount.send(delivered_to(message))
        assert mount.followed == (resource_topic("build", "42"),), "following is what the render read"

    posts = SimpleNamespace(pending_generation=AsyncMock(return_value=7))
    reconciler = SimpleNamespace(reconcile=AsyncMock())
    bot = RedstoneSquid.__new__(RedstoneSquid)
    bot.topic_bus = bus
    bot.topic_publisher = bus
    bot.services = cast(Any, SimpleNamespace(posts=posts))
    bot.post_reconciler = cast(Any, reconciler)
    source = "after"

    await RedstoneSquid.refresh_posts(bot, "build", "42")
    await _drain_scheduler(scheduler)

    assert all("after" in str(message.edit.await_args.kwargs["view"].to_components()) for message in messages)
    reconciler.reconcile.assert_awaited_once_with("build", "42", 7)
