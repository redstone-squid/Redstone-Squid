"""Mounted diagnostics refresh cadence; report fetching stays in reactive resources."""

from collections.abc import Callable
from typing import override

import anyio
from anyio.abc import TaskStatus

from squid_ui.runtime import ManagedService


class ErrorReportRefresh(ManagedService):
    """Request diagnostic updates until the owning component cancels ``run``."""

    def __init__(self, invalidate: Callable[[], None], *, interval: float = 30.0) -> None:
        if interval <= 0:
            message = "diagnostic refresh interval must be positive"
            raise ValueError(message)
        self._invalidate = invalidate
        self._interval = interval

    @override
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        task_status.started()
        while True:
            await anyio.sleep(self._interval)
            self._invalidate()
