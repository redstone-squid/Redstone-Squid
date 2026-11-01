"""Mounted diagnostics refresh cadence; report fetching stays in reactive resources."""

from typing import override

import anyio
from anyio.abc import TaskStatus

from squid_ui.runtime import ManagedService, Resource


class ErrorReportRefresh(ManagedService):
    """Request diagnostic updates until the owning component cancels ``run``."""

    def __init__(self, changes: Resource[bool], *, interval: float = 30.0) -> None:
        if interval <= 0:
            message = "diagnostic refresh interval must be positive"
            raise ValueError(message)
        self.changes = changes
        self._interval = interval

    @override
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        """Check once before signalling readiness, then request periodic comparisons.

        A failed query stays in the resource's status so the reader can retry without
        losing the open report. Readiness means the first attempt settled, not that
        the database is healthy. Unmounting cancels a still-running initial check.
        """
        await self.changes.reload()
        task_status.started()
        while True:
            await anyio.sleep(self._interval)
            self.changes.invalidate()
