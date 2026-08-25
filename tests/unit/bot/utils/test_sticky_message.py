"""Unit tests for the reusable StickyMessage coordinator."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import TextChannel

import squid_discord as sd
from squid.bot.utils.sticky_message import FunctionalStickyMessage, StickyMessage


class StubStickyMessage(StickyMessage):
    def __init__(self, *, stale_threshold: int = 3, debounce_delay: float = 0.05) -> None:
        super().__init__(stale_threshold=stale_threshold, debounce_delay=debounce_delay)
        self.render_count = 0

    async def render(self, channel: TextChannel) -> sd.presentation.DiscordPresentation:
        self.render_count += 1
        return sd.render_static([])


def _make_channel(channel_id: int = 12345) -> Any:
    channel = MagicMock(spec=TextChannel)
    channel.id = channel_id
    channel.send = AsyncMock(side_effect=lambda **_kwargs: MagicMock(id=channel.send.call_count + 100))
    mock_partial = MagicMock()
    mock_partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=mock_partial)
    return channel


async def test_first_trigger_sends_sticky_message_immediately() -> None:
    sticky = StubStickyMessage()
    channel = _make_channel()

    await sticky.trigger(channel)

    assert sticky.is_active_in(channel.id)
    assert sticky.get_message_id(channel.id) == 101
    assert channel.send.await_count == 1
    assert sticky.render_count == 1


async def test_subsequent_trigger_within_stale_threshold_debounces() -> None:
    sticky = StubStickyMessage(stale_threshold=3, debounce_delay=0.05)
    channel = _make_channel()

    await sticky.trigger(channel)
    assert channel.send.await_count == 1

    # Second trigger: staleness is 1 (< 3), so it shouldn't send immediately
    await sticky.trigger(channel)
    assert channel.send.await_count == 1

    # Wait for debounce timer to fire
    await asyncio.sleep(0.08)
    assert channel.send.await_count == 2
    assert sticky.get_message_id(channel.id) == 102


async def test_reaching_stale_threshold_repositions_immediately() -> None:
    sticky = StubStickyMessage(stale_threshold=3, debounce_delay=1.0)
    channel = _make_channel()

    # Initial trigger
    await sticky.trigger(channel)
    assert channel.send.await_count == 1

    # Activity 1 & 2
    sticky.record_activity(channel.id)
    sticky.record_activity(channel.id)
    assert channel.send.await_count == 1

    # 3rd activity via trigger (staleness reaches 3) -> immediate reposition
    await sticky.trigger(channel)
    assert channel.send.await_count == 2
    assert sticky.get_message_id(channel.id) == 102


async def test_reposition_deletes_old_message_and_resets_staleness() -> None:
    sticky = StubStickyMessage()
    channel = _make_channel()

    await sticky.trigger(channel)
    first_id = sticky.get_message_id(channel.id)
    assert first_id == 101

    await sticky.reposition(channel)
    second_id = sticky.get_message_id(channel.id)
    assert second_id == 102

    channel.get_partial_message.assert_called_with(101)
    partial_msg = channel.get_partial_message.return_value
    partial_msg.delete.assert_awaited_once()


async def test_reposition_tolerates_already_deleted_message() -> None:
    sticky = StubStickyMessage()
    channel = _make_channel()
    partial_msg = channel.get_partial_message.return_value
    partial_msg.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "Message not found"))

    await sticky.trigger(channel)
    # Should not raise
    await sticky.reposition(channel)
    assert sticky.get_message_id(channel.id) == 102


async def test_dismiss_deletes_message_and_clears_state() -> None:
    sticky = StubStickyMessage()
    channel = _make_channel()

    await sticky.trigger(channel)
    assert sticky.is_active_in(channel.id)

    await sticky.dismiss(channel)
    assert not sticky.is_active_in(channel.id)
    assert sticky.get_message_id(channel.id) is None
    partial_msg = channel.get_partial_message.return_value
    partial_msg.delete.assert_awaited_once()


async def test_functional_sticky_message_uses_renderer_callback() -> None:
    called = False

    async def custom_render(ch: TextChannel) -> sd.presentation.DiscordPresentation:
        nonlocal called
        called = True
        return sd.render_static([])

    sticky = FunctionalStickyMessage(custom_render)
    channel = _make_channel()

    await sticky.trigger(channel)
    assert called
    assert sticky.is_active_in(channel.id)
