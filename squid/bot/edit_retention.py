"""Keep attachment files alive while a draft editor's session history can restore them."""

import asyncio
import logging
from typing import override
from uuid import UUID

import anyio
from anyio.abc import TaskStatus

from squid.bot.edit_controller import EditController
from squid.edits.domain import EditReceipt
from squid.edits.retention import EditRetentionService
from squid_ui.runtime import ManagedService

logger = logging.getLogger(__name__)


class EditHistoryRetention(ManagedService):
    """Renew resource leases for receipts in one editor's undo and redo history.

    An edit that removes an attachment leases the file for 120 seconds so cleanup
    keeps it while undo could still restore it. This service renews the leases of
    receipts the controller still holds, releases the rest, and releases everything
    when its owner unmounts. It observes the controller from construction, so it
    also releases leases for receipts that left history before the first renewal.
    """

    def __init__(self, controller: EditController, service: EditRetentionService) -> None:
        self._controller = controller
        self._service = service
        self._owned: set[UUID] = set()
        self._changed = asyncio.Event()
        controller.observe(self)

    def receipt_created(self, receipt: EditReceipt) -> None:
        if receipt.resources:
            self._owned.add(receipt.command.operation_id)

    def receipts_changed(self) -> None:
        self._changed.set()

    @property
    def owned(self) -> frozenset[UUID]:
        """Operations whose leases this service has not yet released."""
        return frozenset(self._owned)

    def retained(self) -> frozenset[UUID]:
        """Operations whose leases should stay alive now."""
        live = frozenset(receipt.command.operation_id for receipt in self._controller.receipts if receipt.resources)
        # While an undo or redo runs, its new receipt is not in history yet. Keep every
        # lease until History has swapped the receipts, even through a slow refresh.
        return live | self.owned if self._controller.busy else live

    async def sync(self) -> None:
        retained = self.retained()
        released = self.owned - retained
        if retained or released:
            await self._service.sync(retained, released)
            self._owned.difference_update(released)

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
                with anyio.move_on_after(30):
                    await self._changed.wait()
                self._changed.clear()
        finally:
            released = self.owned
            if released:
                with anyio.move_on_after(10, shield=True):
                    await self._service.sync(frozenset(), released)
                    self._owned.difference_update(released)
