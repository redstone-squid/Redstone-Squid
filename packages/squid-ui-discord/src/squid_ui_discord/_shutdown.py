"""One supervised shutdown and the result shared by all its observers."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import anyio

from squid_ui.runtime.services import ServiceHost
from squid_ui.runtime.tasks import task_is_within

_logger = logging.getLogger(__name__)


class Shutdown:
    """Hold one shutdown result; ``wait`` observes completion without owning the work."""

    def __init__(self) -> None:
        self.accepted = False
        self.done = asyncio.Event()
        self.runner: int | None = None
        self.error: Exception | None = None

    def start(self, host: ServiceHost | None, work: Callable[[], Awaitable[None]]) -> None:
        if self.accepted:
            return
        if host is None:
            message = "finishing requires a running ServiceHost; pass service_host when constructing the root"
            raise RuntimeError(message)

        async def run() -> None:
            self.runner = anyio.get_current_task().id
            try:
                await work()
            except Exception as error:
                self.error = error
                _logger.exception("shutdown failed")
            finally:
                self.runner = None
                self.done.set()

        host.start_cleanup(run)
        self.accepted = True

    def check_wait(self) -> None:
        if self.runner is not None and task_is_within({self.runner}):
            message = "shutdown activity cannot await its own completion; use finish(wait=False)"
            raise RuntimeError(message)

    async def wait(self) -> None:
        self.check_wait()
        await self.done.wait()
        if self.error is not None:
            raise self.error
