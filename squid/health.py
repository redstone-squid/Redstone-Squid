"""Small process-local HTTP health server for non-HTTP services."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Self

from aiohttp import web

logger = logging.getLogger(__name__)
type ReadinessCheck = Callable[[], Awaitable[bool]]


class ProcessHealthServer:
    """Expose liveness and dependency-aware readiness for one process."""

    def __init__(
        self,
        readiness: ReadinessCheck,
        *,
        port: int,
        host: str = "127.0.0.1",
        timeout_seconds: float = 3.0,
    ) -> None:
        self._readiness = readiness
        self._port = port
        self._host = host
        self._timeout_seconds = timeout_seconds
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Bind the health listener after process resources exist."""
        application = web.Application()
        application.router.add_get("/livez", self._live)
        application.router.add_get("/readyz", self._ready)
        self._runner = web.AppRunner(application, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()

    async def close(self) -> None:
        """Stop accepting health probes."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def _live(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"status": "ok"})

    async def _ready(self, request: web.Request) -> web.Response:
        del request
        try:
            async with asyncio.timeout(self._timeout_seconds):
                ready = await self._readiness()
        except Exception:
            logger.warning("Process readiness check failed", exc_info=True)
            ready = False
        status = 200 if ready else 503
        return web.json_response({"status": "ready" if ready else "not_ready"}, status=status)
