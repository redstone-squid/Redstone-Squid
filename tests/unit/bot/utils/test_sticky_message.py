"""Sticky message delivery and timer ownership at the Discord boundary."""

from types import SimpleNamespace
from typing import cast, override

import aiohttp
import anyio
import discord
import pytest

import squid_ui_discord as sd
from squid.bot.utils.sticky_message import StickyMessage
from squid.runtime import BackgroundTaskSupervisor, JobHandle
from squid_ui_discord.testing import MessageHarness


class StubStickyMessage(StickyMessage):
    @override
    async def render(self, channel: discord.TextChannel) -> sd.message_payload.MessagePayload:
        return sd.render_static([])


class ChannelHarness:
    """Record deliveries with real cancellation points at the network boundary."""

    id = 12345

    def __init__(self) -> None:
        self.messages: list[MessageHarness] = []
        self.repositioned = anyio.Event()
        self.delete_started = anyio.Event()
        self.delete_finished = anyio.Event()
        self.block_delete = False

    @property
    def source(self) -> discord.TextChannel:
        return cast(discord.TextChannel, self)

    async def send(self, **kwargs: object) -> discord.Message:
        await anyio.lowlevel.checkpoint()
        message = MessageHarness(message_id=len(self.messages) + 101, channel_id=self.id)
        message.delete.callback = self._delete
        self.messages.append(message)
        if len(self.messages) > 1:
            self.repositioned.set()
        return message.source

    def get_partial_message(self, message_id: int) -> discord.PartialMessage:
        return next(message.source for message in self.messages if message.id == message_id)

    async def _delete(self) -> None:
        self.delete_started.set()
        try:
            await anyio.lowlevel.checkpoint()
            if self.block_delete:
                await anyio.sleep_forever()
        finally:
            self.delete_finished.set()


@pytest.fixture
def channel() -> ChannelHarness:
    return ChannelHarness()


async def test_debounce_completes_delivery_across_network_checkpoints(channel: ChannelHarness) -> None:
    async with BackgroundTaskSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor, debounce_delay=0)
        await sticky.trigger(channel.source)
        assert sticky.get_message_id(channel.id) == 101

        await sticky.trigger(channel.source)
        await sticky.trigger(channel.source)
        assert len(channel.messages) == 1
        with anyio.fail_after(1):
            await channel.repositioned.wait()

        assert sticky.get_message_id(channel.id) == 102
        assert len(channel.messages[0].deletions) == 1
        await sticky.close()


async def test_stale_threshold_repositions_and_cancels_pending_timer(channel: ChannelHarness) -> None:
    async with BackgroundTaskSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor, debounce_delay=60)
        await sticky.trigger(channel.source)
        await sticky.trigger(channel.source)
        sticky.record_activity(channel.id)
        await sticky.trigger(channel.source)

        assert sticky.get_message_id(channel.id) == 102
        assert len(channel.messages[0].deletions) == 1
        assert not sticky._debounce_tasks
        await sticky.close()


async def test_dismiss_cancels_pending_timer_and_removes_message(channel: ChannelHarness) -> None:
    async with BackgroundTaskSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor, debounce_delay=60)
        await sticky.trigger(channel.source)
        await sticky.trigger(channel.source)
        await sticky.dismiss(channel.source)

        assert not sticky.is_active_in(channel.id)
        assert sticky.get_message_id(channel.id) is None
        assert len(channel.messages[0].deletions) == 1
        assert not sticky._debounce_tasks
        await sticky.close()


async def test_close_cancels_and_settles_inflight_delivery(channel: ChannelHarness) -> None:
    async with BackgroundTaskSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor, debounce_delay=0)
        channel.block_delete = True
        await sticky.trigger(channel.source)
        await sticky.trigger(channel.source)
        with anyio.fail_after(1):
            await channel.delete_started.wait()
            await sticky.close()

        assert channel.delete_finished.is_set()
        assert len(channel.messages) == 1
        with pytest.raises(RuntimeError, match="after closing"):
            await sticky.trigger(channel.source)


async def test_close_retains_timer_scheduled_while_another_is_cancelled(channel: ChannelHarness) -> None:
    cancelling = anyio.Event()
    resume_cancellation = anyio.Event()

    class GatedSupervisor(BackgroundTaskSupervisor):
        @override
        async def cancel(self, *handles: JobHandle) -> None:
            cancelling.set()
            await resume_cancellation.wait()
            await super().cancel(*handles)

    async with GatedSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor, debounce_delay=60)
        await sticky.trigger(channel.source)
        await sticky.trigger(channel.source)
        with anyio.fail_after(1):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(sticky.reposition, channel.source)
                await cancelling.wait()
                await sticky.trigger(channel.source)
                replacement = sticky._debounce_tasks[channel.id]
                resume_cancellation.set()

        assert sticky._debounce_tasks[channel.id] is replacement
        await sticky.close()
        assert replacement.finished.is_set()
        assert len(channel.messages) == 2


async def test_reposition_tolerates_already_deleted_message(channel: ChannelHarness) -> None:
    async with BackgroundTaskSupervisor().running() as supervisor:
        sticky = StubStickyMessage(supervisor)
        await sticky.trigger(channel.source)
        response = cast(aiohttp.ClientResponse, SimpleNamespace(status=404, reason="Not Found"))
        channel.messages[0].delete.error = discord.NotFound(response, "Message not found")
        await sticky.reposition(channel.source)

        assert sticky.get_message_id(channel.id) == 102
        await sticky.close()
