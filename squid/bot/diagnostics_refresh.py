"""Mounted diagnostics refresh cadence; report fetching stays in reactive resources."""

from collections.abc import Awaitable, Callable
from typing import override

import anyio
from anyio.abc import TaskStatus

from squid_ui import resource
from squid_ui.runtime import ManagedService


class ErrorReportRefresh(ManagedService):
    """Request diagnostic updates until the owning component cancels ``run``."""

    def __init__(self, check: Callable[[], Awaitable[bool]], *, interval: float = 30.0) -> None:
        if interval <= 0:
            message = "diagnostic refresh interval must be positive"
            raise ValueError(message)
        self._check = check
        self.changes.replace(value=False)
        self._interval = interval

    @resource
    async def changes(self) -> bool:
        """Compare the current report list with the displayed window."""
        return await self._check()

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
