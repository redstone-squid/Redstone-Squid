"""A parent editor owns resource leases for its shared undo and redo history."""

import logging
from typing import override
from uuid import UUID

import anyio
from anyio.abc import TaskStatus

from squid.bot.edit_controller import EditController
from squid.edits.retention import EditRetentionService
from squid_ui.runtime import ManagedService

logger = logging.getLogger(__name__)


class EditHistoryRetention(ManagedService):
    def __init__(self, controller: EditController, service: EditRetentionService) -> None:
        self._controller = controller
        self._service = service
        self._retained: frozenset[UUID] = frozenset()

    async def sync(self) -> None:
        retained = self._controller.retained_operations()
        if retained or self._retained:
            await self._service.sync(retained, self._retained - retained)
        self._retained = retained

    @override
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        task_status.started()
        try:
            while True:
                try:
                    await self.sync()
                except Exception:
                    # An outage must not keep the parent alive or renew leases forever.
                    logger.warning("Unable to renew editor attachment retention", exc_info=True)
                await anyio.sleep(30)
        finally:
            released = self._retained | self._controller.retained_operations()
            if released:
                with anyio.move_on_after(10, shield=True):
                    await self._service.sync(frozenset(), released)
