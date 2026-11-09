"""Record background registration without starting gateway or database work."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import anyio

from squid.runtime import BackgroundTaskSupervisor, JobHandle


class SchedulingRecorder(BackgroundTaskSupervisor):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[tuple[Coroutine[Any, Any, None], str]] = []
        self.periodic: list[tuple[str, float]] = []

    def start(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> JobHandle:
        self.starts.append((coroutine, name))
        coroutine.close()
        return self._finished(name)

    def start_periodic(
        self, operation: Callable[[], Awaitable[None]], *, name: str, interval: float, run_immediately: bool = True
    ) -> JobHandle:
        self.periodic.append((name, interval))
        return self._finished(name)

    @staticmethod
    def _finished(name: str) -> JobHandle:
        finished = anyio.Event()
        finished.set()
        return JobHandle(name, anyio.CancelScope(), finished)
