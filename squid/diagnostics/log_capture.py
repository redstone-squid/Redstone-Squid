"""Queue logged exceptions for asynchronous storage as error reports.

Queue workers may catch and log a failure without letting it reach a transport's
error handler. This handler captures those logs too. It ignores failures already
marked as captured and failures from diagnostics itself, which would recurse.
The queue is bounded, so a burst of failures can discard older reports.
"""

import contextlib
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override
from uuid import uuid4

if TYPE_CHECKING:
    import asyncio

    from squid.diagnostics.application import ErrorReportService

logger = logging.getLogger(__name__)

CAPTURED_ATTRIBUTE = "squid_error_captured"
"""Set on a log record whose failure was already stored with richer context.

The API, Discord, and background-job handlers store the command name, route, or job that failed
before logging it. Without this flag the log handler would file a second, thinner report for the
same incident.
"""

WORK_LOST_ATTRIBUTE = "squid_work_lost"
"""Mark a logged failure as work that will not be retried.

Queue consumers set this when a job exhausts its retries. The capture handler
copies the flag into the stored report.
"""

LOG_SURFACE = "log"


@dataclass(frozen=True, slots=True)
class _Pending:
    """One captured failure, resolved off the record before it leaves the logging thread."""

    error: BaseException
    correlation: str
    origin: str
    context: dict[str, Any]
    log_tail: tuple[str, ...]
    work_lost: bool


class ErrorReportLogHandler(logging.Handler):
    """Queue logged exceptions for a supervised task to store.

    Logging may run on a worker thread. emit gathers the error details, appends
    them to a bounded deque, and wakes the event loop. run performs the database
    writes. When the queue is full, newer failures replace the oldest entries.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        super().__init__(level=logging.ERROR)
        self._pending: deque[_Pending] = deque(maxlen=capacity)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._attached: list[logging.Logger] = []
        self.dropped = 0
        """Overflow count since the drain task last logged and reset it."""

    def attach_to(self, targets: Sequence[logging.Logger]) -> None:
        """Add this handler to each logger, remembering where so it can be taken off again."""
        for target in targets:
            target.addHandler(self)
            self._attached.append(target)

    def detach(self) -> None:
        """Remove this handler from every logger it was attached to.

        The drain task calls this on exit so the handler stops collecting reports
        after storage has stopped.
        """
        for target in self._attached:
            target.removeHandler(self)
        self._attached.clear()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            pending = self._resolve(record)
        except Exception:
            self.handleError(record)
            return
        if pending is None:
            return

        if len(self._pending) == self._pending.maxlen:
            self.dropped += 1
        self._pending.append(pending)
        self._notify()

    def _resolve(self, record: logging.LogRecord) -> _Pending | None:
        """Decide whether this record is a failure worth storing, and read what it needs."""
        error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
        if error is None:
            return None
        if getattr(record, CAPTURED_ATTRIBUTE, False):
            return None
        # Recording a failure can itself log an error. Capturing that error would recurse.
        if record.name.startswith(__package__ or "squid.diagnostics"):
            return None

        correlation = getattr(record, "request_id", None)
        if not isinstance(correlation, str) or not correlation:
            # Logs outside a request still need a reference for report lookup.
            correlation = uuid4().hex[:12]

        from squid.observability import correlated_log_buffer

        buffer = correlated_log_buffer()
        return _Pending(
            error=error,
            correlation=correlation,
            origin=record.name,
            context={"logger": record.name, "message": record.getMessage()},
            log_tail=buffer.snapshot(correlation) if buffer is not None else (),
            work_lost=bool(getattr(record, WORK_LOST_ATTRIBUTE, False)),
        )

    def _notify(self) -> None:
        """Wake the drain task from whichever thread logged."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        # The loop is closing or already closed; the process is going away with it.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(wake.set)

    async def run(self, service: ErrorReportService) -> None:
        """Store queued failures until cancelled, then detach the handler.

        Run this through the process supervisor. Cancellation does not flush the
        remaining queue; those reports may be lost during shutdown.
        """
        import asyncio

        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        try:
            # Drain failures logged before startup without waiting for another log record.
            await self._store_queued(service)
            while True:
                await self._wake.wait()
                self._wake.clear()
                await self._store_queued(service)
        finally:
            self._loop = None
            self._wake = None
            self.detach()

    async def _store_queued(self, service: ErrorReportService) -> None:
        from squid.observability import correlation_reference

        while self._pending:
            pending = self._pending.popleft()
            try:
                await service.record(
                    pending.error,
                    correlation_id=pending.correlation,
                    reference=correlation_reference(pending.correlation),
                    surface=LOG_SURFACE,
                    origin=pending.origin,
                    context=pending.context,
                    log_tail=pending.log_tail,
                    work_lost=pending.work_lost,
                )
            except Exception:
                # A replacement service may raise even though ErrorReportService catches
                # ordinary errors. Keep draining so one failure does not stop later reports.
                logger.exception("Could not store a logged failure [origin=%s]", pending.origin)
        if self.dropped:
            dropped, self.dropped = self.dropped, 0
            logger.warning("Dropped %d logged failures before they could be stored", dropped)


def captured() -> dict[str, bool]:
    """Log-record extras marking a failure this code already stored itself."""
    return {CAPTURED_ATTRIBUTE: True}


def work_lost() -> dict[str, bool]:
    """Log-record extras marking a failure that permanently abandoned work."""
    return {WORK_LOST_ATTRIBUTE: True}


def install_log_capture(*, capacity: int = 256) -> ErrorReportLogHandler:
    """Replace existing capture handlers and attach a new one to configured loggers.

    Attach to root and to loggers with handlers and propagate=False. The latter
    stop records before they reach root, so root alone would miss their failures.
    Installation does not start storage; the caller must supervise handler.run.
    """
    handler = ErrorReportLogHandler(capacity=capacity)
    handler.set_name("error_report_capture")
    targets = _capture_points()
    for target in targets:
        for existing in list(target.handlers):
            if isinstance(existing, ErrorReportLogHandler):
                existing.detach()
    handler.attach_to(targets)
    return handler


def _capture_points() -> list[logging.Logger]:
    """Root, plus every configured logger that keeps its records to itself."""
    targets = [logging.getLogger()]
    manager = logging.getLogger().manager
    for name, existing in list(manager.loggerDict.items()):
        if isinstance(existing, logging.Logger) and not existing.propagate and existing.handlers:
            targets.append(logging.getLogger(name))
    return targets


__all__ = [
    "CAPTURED_ATTRIBUTE",
    "LOG_SURFACE",
    "WORK_LOST_ATTRIBUTE",
    "ErrorReportLogHandler",
    "captured",
    "install_log_capture",
    "work_lost",
]
