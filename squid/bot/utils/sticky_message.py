"""Reusable sticky message coordinator with debounced channel repositioning."""

import abc
import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, override

import discord
from discord import TextChannel

import squid_ui_discord as sd
from squid_ui_discord import send_to

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD = 3
DEFAULT_DEBOUNCE_DELAY = 5.0


class StickyMessage(abc.ABC):
    """One bot message kept at the bottom of each channel by deleting and re-sending it.

    Repositioning is debounced against rate limits: `trigger` reposts at once after `stale_threshold` messages,
    otherwise on a `debounce_delay` trailing timer.
    """

    def __init__(
        self,
        *,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
        debounce_delay: float = DEFAULT_DEBOUNCE_DELAY,
    ) -> None:
        self.stale_threshold = stale_threshold
        self.debounce_delay = debounce_delay
        self._last_message_id: dict[int, int] = {}
        self._messages_since_reposition: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._debounce_tasks: dict[int, asyncio.Task[None]] = {}

    def _lock_for(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    @abc.abstractmethod
    async def render(self, channel: TextChannel) -> sd.message_payload.MessagePayload:
        """The payload to post; called under the channel's lock on every reposition."""
        ...

    def is_active_in(self, channel_id: int) -> bool:
        return channel_id in self._last_message_id

    def get_message_id(self, channel_id: int) -> int | None:
        return self._last_message_id.get(channel_id)

    def record_activity(self, channel_id: int) -> None:
        """Count one message toward the stale threshold; a channel with no sticky is ignored."""
        if channel_id in self._last_message_id:
            self._messages_since_reposition[channel_id] = self._messages_since_reposition.get(channel_id, 0) + 1

    async def trigger(self, channel: TextChannel) -> None:
        """Reposition now if the channel has no sticky or `stale_threshold` is reached; otherwise debounce."""
        channel_id = channel.id
        self.record_activity(channel_id)
        staleness = self._messages_since_reposition.get(channel_id, 0)
        has_message = channel_id in self._last_message_id

        if not has_message or staleness >= self.stale_threshold:
            await self.reposition(channel)
        else:
            self._schedule_debounce(channel)

    def _schedule_debounce(self, channel: TextChannel) -> None:
        channel_id = channel.id
        existing = self._debounce_tasks.get(channel_id)
        if existing is not None and not existing.done():
            return

        async def _delayed_reposition() -> None:
            try:
                await asyncio.sleep(self.debounce_delay)
                await self.reposition(channel)
            except asyncio.CancelledError:
                pass

        self._debounce_tasks[channel_id] = asyncio.create_task(_delayed_reposition())

    async def reposition(self, channel: TextChannel) -> None:
        """Delete the old sticky and post a new one now, cancelling a pending debounce; send failures are logged."""
        async with self._lock_for(channel.id):
            task = self._debounce_tasks.pop(channel.id, None)
            if task is not None and not task.done():
                task.cancel()

            old_id = self._last_message_id.get(channel.id)
            if old_id is not None:
                try:
                    old_msg = channel.get_partial_message(old_id)
                    await old_msg.delete()
                except discord.NotFound, discord.Forbidden, discord.HTTPException:
                    logger.debug(
                        "Could not delete previous sticky message %s in %s",
                        old_id,
                        channel.id,
                        exc_info=True,
                    )

            presentation = await self.render(channel)
            try:
                result = await send_to(channel)(presentation)
                new_msg = result.message
                if new_msg is None:
                    logger.warning("Sticky delivery returned no message for channel %s", channel.id)
                    return
                self._last_message_id[channel.id] = new_msg.id
                self._messages_since_reposition[channel.id] = 0
            except discord.Forbidden, discord.HTTPException:
                logger.warning("Failed to send sticky message in channel %s", channel.id, exc_info=True)

    async def dismiss(self, channel: TextChannel) -> None:
        """Delete the sticky and stop tracking the channel, cancelling a pending debounce."""
        async with self._lock_for(channel.id):
            task = self._debounce_tasks.pop(channel.id, None)
            if task is not None and not task.done():
                task.cancel()

            old_id = self._last_message_id.pop(channel.id, None)
            self._messages_since_reposition.pop(channel.id, None)
            if old_id is not None:
                try:
                    old_msg = channel.get_partial_message(old_id)
                    await old_msg.delete()
                except discord.NotFound, discord.Forbidden, discord.HTTPException:
                    pass


class FunctionalStickyMessage(StickyMessage):
    def __init__(
        self,
        renderer: Callable[[TextChannel], Coroutine[Any, Any, sd.message_payload.MessagePayload]],
        *,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
        debounce_delay: float = DEFAULT_DEBOUNCE_DELAY,
    ) -> None:
        super().__init__(stale_threshold=stale_threshold, debounce_delay=debounce_delay)
        self._renderer = renderer

    @override
    async def render(self, channel: TextChannel) -> sd.message_payload.MessagePayload:
        return await self._renderer(channel)
