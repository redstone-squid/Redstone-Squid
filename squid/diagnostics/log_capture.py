"""Turn logged exceptions into stored error reports.

The transports capture their own failures, with the command name or route that produced them. The
worker mostly cannot: its queue consumers are built to absorb a failure, dead-letter the job, log
it, and carry on, so nothing ever reaches the supervisor that would have captured it. Those are
exactly the failures an operator sees in `docker logs` and then cannot look up.

So the store also follows the logs. Anything logged at ERROR or above with an exception attached
becomes a report, which makes the store mirror the container output by construction and covers
code written later without it having to know this exists.
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
"""Set on a log record for a failure that permanently abandoned work.

A queue consumer that dead-letters a job sets this on the line it already logs, which is the whole
of what it has to do to mark the report -- no service, no injection, no new dependency pointing at
a context it should not know about.
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
    """Queue logged exceptions for storage, without blocking or awaiting on the logging path.

    `emit` is synchronous and may run on any thread -- the bot logs through a `QueueListener`, and
    anything inside `to_thread` logs from a worker thread -- while storing a report is an awaited
    database write. So `emit` only appends to a bounded deque and nudges the loop; a supervised
    task does the writing.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        super().__init__(level=logging.ERROR)
        self._pending: deque[_Pending] = deque(maxlen=capacity)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._attached: list[logging.Logger] = []
        self.dropped = 0
        """Failures discarded because the queue was full. Reported, never silently zero."""

    def attach_to(self, targets: Sequence[logging.Logger]) -> None:
        """Add this handler to each logger, remembering where so it can be taken off again."""
        for target in targets:
            target.addHandler(self)
            self._attached.append(target)

    def detach(self) -> None:
        """Remove this handler from every logger it was added to.

        Called when the drain task stops, so a handler with nothing draining it cannot go on
        collecting failures nobody will store -- which in a test process means one suite's
        handler quietly queueing another's.
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
        # Storing a report logs when it fails, and that log line carries an exception. Following it
        # would try to store a report about failing to store a report, forever.
        if record.name.startswith(__package__ or "squid.diagnostics"):
            return None

        correlation = getattr(record, "request_id", None)
        if not isinstance(correlation, str) or not correlation:
            # Nothing bound a correlation, so this failure is its own incident. It still gets a
            # reference, because an unreferenceable report cannot be looked up at all.
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
        """Store queued failures until cancelled. Owned by the process supervisor."""
        import asyncio

        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        try:
            # Anything logged before the loop was captured is still queued, so drain once up front
            # rather than waiting for the next failure to wake us.
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
                # `ErrorReportService.record` swallows, so reaching here means something outside
                # it broke. This loop is the only thing storing worker failures for the life of
                # the process; letting one bad write end it would lose every later failure too.
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
    """Attach the handler wherever records actually stop, and return it.

    The root logger alone is not enough. `build_logging_config` gives `squid`, `discord` and the
    uvicorn loggers `propagate = False`, so a record from `squid.search…` is handled at `squid`
    and never reaches root -- which is every failure this is meant to catch. The handler therefore
    goes on root *and* on each non-propagating logger. A record is still stored once, because
    propagation stopping is exactly what makes those sets disjoint.
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
