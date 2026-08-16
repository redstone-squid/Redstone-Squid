"""Log-driven capture: what becomes a report, and what must never become one."""

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import cast

import pytest

from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.log_capture import (
    ErrorReportLogHandler,
    captured,
    install_log_capture,
    work_lost,
)
from squid.observability import CorrelatedLogBuffer


class RecordingService:
    def __init__(self, *, failing: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._failing = failing

    async def record(self, error: BaseException, **kwargs: object) -> None:
        if self._failing:
            msg = "the database is down"
            raise RuntimeError(msg)
        self.calls.append({"error": error, **kwargs})


def raised(message: str = "boom") -> Exception:
    try:
        _throw(message)
    except RuntimeError as error:
        return error
    msg = "the helper must have raised"
    raise AssertionError(msg)


def _throw(message: str) -> None:
    raise RuntimeError(message)


def log_record(
    *,
    name: str = "squid.search.infrastructure.projection",
    level: int = logging.ERROR,
    message: str = "Dead-lettered a search projection",
    error: BaseException | None = None,
    request_id: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> logging.LogRecord:
    exc_info = (type(error), error, error.__traceback__) if error is not None else None
    record = logging.LogRecord(name, level, __file__, 1, message, (), exc_info)  # pyright: ignore[reportArgumentType]
    if request_id is not None:
        record.request_id = request_id
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


async def drain(handler: ErrorReportLogHandler, service: RecordingService) -> None:
    """Run the drain long enough for queued failures to be stored."""
    task = asyncio.create_task(handler.run(cast(ErrorReportService, service)))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_a_logged_exception_becomes_a_report() -> None:
    """The worker absorbs its failures, so the log line is the only thing left to follow."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(error=raised(), request_id="a" * 32))

    await drain(handler, service)

    (call,) = service.calls
    assert call["correlation_id"] == "a" * 32
    assert call["reference"] == "a" * 12
    assert call["surface"] == "log"
    assert call["origin"] == "squid.search.infrastructure.projection"
    assert call["work_lost"] is False


async def test_a_dead_lettered_job_is_marked_as_losing_work() -> None:
    """Nothing will retry it, which is what separates it from an exception something survived."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(error=raised(), request_id="b" * 32, extra=work_lost()))

    await drain(handler, service)

    assert service.calls[0]["work_lost"] is True


async def test_a_record_without_an_exception_is_ignored() -> None:
    """An error message is not an incident; a traceback is."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(message="queue drained with failures", request_id="c" * 32))

    await drain(handler, service)

    assert service.calls == []


async def test_an_already_captured_failure_is_not_stored_twice() -> None:
    """The transports store their failure with the route or command that caused it, then log it."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(error=raised(), request_id="d" * 32, extra=captured()))

    await drain(handler, service)

    assert service.calls == []


async def test_failures_from_this_package_are_ignored() -> None:
    """Storing a report logs when it fails, and following that would never terminate."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(name="squid.diagnostics.application", error=raised(), request_id="e" * 32))

    await drain(handler, service)

    assert service.calls == []


async def test_an_uncorrelated_failure_still_gets_a_reference() -> None:
    """A report nobody can look up is not worth storing."""
    handler = ErrorReportLogHandler()
    service = RecordingService()
    handler.handle(log_record(error=raised()))

    await drain(handler, service)

    correlation = service.calls[0]["correlation_id"]
    assert isinstance(correlation, str)
    assert len(correlation) == 12
    assert service.calls[0]["reference"] == correlation


async def test_the_queue_is_bounded_and_says_what_it_dropped() -> None:
    """A process failing in a tight loop must not grow this without limit."""
    handler = ErrorReportLogHandler(capacity=2)
    service = RecordingService()
    for index in range(5):
        handler.handle(log_record(error=raised(f"boom {index}"), request_id=f"{index}" * 32))

    assert handler.dropped == 3
    await drain(handler, service)

    assert len(service.calls) == 2
    # Newest kept: an old failure matters less than the one still happening.
    assert [str(call["error"]) for call in service.calls] == ["boom 3", "boom 4"]


async def test_the_log_tail_is_read_without_consuming_it() -> None:
    """One run can log several failures, and the first must not take the context from the rest."""
    buffer = CorrelatedLogBuffer(max_records=5)
    buffer.setFormatter(logging.Formatter("%(message)s"))
    # `set_name` is what registers a handler for `logging.getHandlerByName`, which is how
    # `correlated_log_buffer()` finds it.
    buffer.set_name("correlation_buffer")
    try:
        context = logging.LogRecord("squid.test", logging.INFO, __file__, 1, "claimed 3 items", (), None)
        context.request_id = "f" * 32
        buffer.handle(context)

        handler = ErrorReportLogHandler()
        service = RecordingService()
        handler.handle(log_record(error=raised("first"), request_id="f" * 32))
        handler.handle(log_record(error=raised("second"), request_id="f" * 32))
        await drain(handler, service)
    finally:
        buffer.set_name("")

    assert [call["log_tail"] for call in service.calls] == [("claimed 3 items",), ("claimed 3 items",)]


async def test_a_failing_store_does_not_stop_the_drain(caplog: pytest.LogCaptureFixture) -> None:
    """This loop is the only thing storing worker failures for the life of the process.

    `ErrorReportService.record` swallows, so a raise here means something outside it broke. Ending
    the loop over one bad write would silently lose every later failure as well.
    """
    handler = ErrorReportLogHandler()
    service = RecordingService(failing=True)
    handler.handle(log_record(error=raised(), request_id="0" * 32))

    with caplog.at_level("ERROR"):
        await drain(handler, service)

    assert "Could not store a logged failure" in caplog.text

    # The loop is still live: a later failure is still stored once the store recovers.
    service._failing = False
    handler.handle(log_record(error=raised("later"), request_id="1" * 32))
    await drain(handler, service)

    assert [str(call["error"]) for call in service.calls] == ["later"]


def test_install_reaches_loggers_that_do_not_propagate() -> None:
    """`squid` has `propagate = False`, so a root-only handler would see none of its failures."""
    isolated = logging.getLogger("squid-test-isolated")
    isolated.propagate = False
    isolated.addHandler(logging.NullHandler())
    try:
        handler = install_log_capture()

        assert handler in logging.getLogger().handlers
        assert handler in isolated.handlers
    finally:
        for target in (logging.getLogger(), isolated):
            for existing in list(target.handlers):
                if isinstance(existing, (ErrorReportLogHandler, logging.NullHandler)):
                    target.removeHandler(existing)
