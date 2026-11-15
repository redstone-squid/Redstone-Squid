"""Own the credentials used to edit or delete one mounted Discord message."""

import logging
from dataclasses import dataclass
from typing import cast

from squid_ui.ext.discord import delivery as deliver
from squid_ui.ext.discord.message_payload import MessagePayload
from squid_ui.profiling import OperationRecorder

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MessageDelivery:
    """One message's standing authority, independent of its published render generation.

    Interaction handles are attempted before standing authority. Renewal keeps permanent
    authority and otherwise adopts both edit and delete capabilities of a fresh token.
    """

    message_id: int | None = None
    handle: deliver.EditHandle | None = None
    delete_handle: deliver.DeleteHandle | None = None

    def renew(self, handle: deliver.EditHandle) -> None:
        if self.handle is not None and self.handle.permanent:
            return
        self.adopt(handle)

    def adopt(self, handle: deliver.EditHandle) -> bool:
        """Accept replacement authority unless it would downgrade a permanent handle."""
        if self.handle is not None and self.handle.permanent and not handle.permanent:
            return False
        self.handle = handle
        if hasattr(handle, "delete"):
            self.delete_handle = cast(deliver.DeleteHandle, handle)
        return True

    async def write(
        self,
        payload: MessagePayload,
        *,
        keep_attachments: bool,
        through: deliver.EditHandle | None,
        profile: OperationRecorder | None = None,
    ) -> deliver.EditHandle | None:
        """Try interaction then standing authority, forgetting a stale standing handle."""
        for handle in (through, self.handle):
            if handle is None or handle.expired():
                continue
            try:
                if profile is None:
                    await handle.write(payload, keep_attachments=keep_attachments)
                else:
                    source = "interaction" if handle is through else "standing"
                    handle_type = f"{type(handle).__module__}.{type(handle).__qualname__}"
                    with profile.span("discord_write", attributes={"source": source, "handle": handle_type}):
                        await handle.write(payload, keep_attachments=keep_attachments)
            except deliver.StaleHandleError:
                logger.debug("discarded a stale edit handle for message %s", self.message_id, exc_info=True)
                if handle is self.handle:
                    self.handle = None
                continue
            return handle
        return None
