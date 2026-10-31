"""Reusable sticky message coordinator with debounced channel repositioning."""

import abc
import asyncio
import logging

import anyio
import discord
from discord import TextChannel

import squid_ui_discord as sd
from squid.runtime import BackgroundTaskSupervisor, JobHandle
from squid_ui_discord import send_to

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD = 3
DEFAULT_DEBOUNCE_DELAY = 5.0


class StickyMessage(abc.ABC):
    """Coordinate sticky channel messages until close cancels their pending work.

    Maintains a single message per channel by deleting the prior instance and sending a new one.
    To avoid rate-limit churn and message spam during active conversations, repositioning is
    debounced: it allows up to `stale_threshold` messages before forcing an immediate reposition,
    and uses a trailing timer (`debounce_delay`) during quiet periods.
    """

    def __init__(
        self,
        supervisor: BackgroundTaskSupervisor,
        *,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
        debounce_delay: float = DEFAULT_DEBOUNCE_DELAY,
    ) -> None:
        self._supervisor = supervisor
        self._closed = False
        self.stale_threshold = stale_threshold
        self.debounce_delay = debounce_delay
        self._last_message_id: dict[int, int] = {}
        self._messages_since_reposition: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._debounce_tasks: dict[int, JobHandle] = {}

    def _lock_for(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    @abc.abstractmethod
    async def render(self, channel: TextChannel) -> sd.message_payload.MessagePayload:
        """Render the presentation to display in the sticky message."""
        ...

    def is_active_in(self, channel_id: int) -> bool:
        """Whether a sticky message is currently tracked in the given channel."""
        return channel_id in self._last_message_id

    def get_message_id(self, channel_id: int) -> int | None:
        """Return the ID of the current sticky message in the channel, if any."""
        return self._last_message_id.get(channel_id)

    def record_activity(self, channel_id: int) -> None:
        """Record general message activity in the channel to track staleness."""
        if channel_id in self._last_message_id:
            self._messages_since_reposition[channel_id] = self._messages_since_reposition.get(channel_id, 0) + 1

    async def trigger(self, channel: TextChannel) -> None:
        """Request the sticky message be posted or refreshed in the channel.

        If no sticky message exists or the staleness threshold has been reached, repositions
        immediately. Otherwise, schedules a trailing debounce timer.
        """
        self._require_open()
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
        if existing is not None and not existing.finished.is_set():
            return

        async def _delayed_reposition() -> None:
            try:
                await anyio.sleep(self.debounce_delay)
                await self._reposition(channel, cancel_debounce=False)
            finally:
                if self._debounce_tasks.get(channel_id) is handle:
                    self._debounce_tasks.pop(channel_id)

        handle = self._supervisor.start(_delayed_reposition(), name=f"sticky-message-{channel_id}")
        self._debounce_tasks[channel_id] = handle

    async def reposition(self, channel: TextChannel) -> None:
        """Force immediate deletion of the old sticky message and posting of a new one."""
        self._require_open()
        await self._reposition(channel, cancel_debounce=True)

    async def _reposition(self, channel: TextChannel, *, cancel_debounce: bool) -> None:
        async with self._lock_for(channel.id):
            if self._closed:
                return
            if cancel_debounce:
                await self._cancel_debounce(channel.id)
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
        """Delete the current sticky message and remove it from active tracking."""
        async with self._lock_for(channel.id):
            await self._cancel_debounce(channel.id)
            old_id = self._last_message_id.pop(channel.id, None)
            self._messages_since_reposition.pop(channel.id, None)
            if old_id is not None:
                try:
                    old_msg = channel.get_partial_message(old_id)
                    await old_msg.delete()
                except discord.NotFound, discord.Forbidden, discord.HTTPException:
                    pass

    async def close(self) -> None:
        """Stop accepting triggers and settle every pending reposition."""
        self._closed = True
        await self._supervisor.cancel(*tuple(self._debounce_tasks.values()))
        self._debounce_tasks.clear()

    async def _cancel_debounce(self, channel_id: int) -> None:
        handle = self._debounce_tasks.pop(channel_id, None)
        if handle is not None:
            await self._supervisor.cancel(handle)

    def _require_open(self) -> None:
        if self._closed:
            msg = "Sticky messages cannot be refreshed after closing."
            raise RuntimeError(msg)
