"""Reach the messages after the first, for a message root that spreads over several.

A root configured with max_messages above 1 can plan its document into several messages.
The root itself owns the first one. This module holds what it needs to write the others:
where each one is and which credentials currently edit it. What each message shows, and
whether that is current, belongs to `_CandidateLifecycle`.

Continuations are numbered from 1 in reading order; 0 always means the first message.
"""

import logging
from dataclasses import dataclass

import discord

from squid_ui_discord import delivery as deliver
from squid_ui_discord.delivery import _is_stale
from squid_ui_discord.message_payload import MessagePayload

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Slot:
    """One delivered continuation message."""

    message_id: int | None
    handle: deliver.EditHandle | None
    delete_handle: deliver.DeleteHandle | None


class _Continuations:
    """The delivered continuation messages of one root, in reading order."""

    def __init__(self, mount_id: str) -> None:
        self._mount_id = mount_id
        self.slots: list[_Slot] = []
        self.follow_up: deliver.MessageDestination | None = None
        """Sends another message beside the first, when the first delivery offered a way."""

    def index_of(self, message_id: int | None) -> int:
        """Which message has this id; 0 for the first message and for any unknown id."""
        if message_id is not None:
            for index, slot in enumerate(self.slots, start=1):
                if slot.message_id == message_id:
                    return index
        return 0

    def renew(self, index: int, handle: deliver.EditHandle) -> None:
        """Trade up to fresher credentials, as `MessageRoot._renew` does for the first message."""
        slot = self.slots[index - 1]
        if slot.handle is None or not slot.handle.permanent:
            slot.handle = handle

    async def write(
        self,
        index: int,
        payload: MessagePayload,
        *,
        keep_attachments: bool,
        through: deliver.EditHandle | None,
    ) -> deliver.EditHandle | None:
        """Edit one continuation through the first usable handle, and return that handle."""
        slot = self.slots[index - 1]
        for handle in (through, slot.handle):
            if handle is None or handle.expired():
                continue
            try:
                await handle.write(payload, keep_attachments=keep_attachments)
            except deliver.StaleHandleError:
                logger.debug("mount %s discarded a stale handle for message %s", self._mount_id, index, exc_info=True)
                if handle is slot.handle:
                    slot.handle = None
                continue
            return handle
        return None

    async def create(self, payload: MessagePayload, destination: deliver.MessageDestination | None) -> bool:
        """Send the next continuation, returning False when nothing can send it right now."""
        if destination is None:
            return False
        try:
            result = await destination(payload)
        except deliver.StaleHandleError:
            return False
        except discord.HTTPException as error:
            if not _is_stale(error):
                raise
            return False
        self.slots.append(_Slot(result.message_id, result.handle, result.delete_handle))
        return True

    async def trim(self, count: int) -> None:
        """Delete continuations beyond the first `count`, forgetting any that cannot be deleted."""
        while len(self.slots) > count:
            slot = self.slots.pop()
            if slot.delete_handle is None:
                logger.warning("mount %s cannot delete a message it no longer needs", self._mount_id)
                continue
            try:
                await slot.delete_handle.delete()
            except deliver.StaleHandleError, discord.NotFound:
                logger.debug("mount %s could not delete a message it no longer needs", self._mount_id, exc_info=True)
