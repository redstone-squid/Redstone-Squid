"""Error report capture and reference resolution."""

from collections.abc import Sequence

import pytest
from whenever import Instant

from squid.core.errors import ErrorCode, InvalidStateError
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.domain import ErrorReport, ErrorReportNotFoundError
from squid.records.errors import RecordNotFoundError

FROZEN = Instant.from_utc(2026, 8, 17, 12, 0, 0)


class FakeRepository:
    """In-memory stand-in that can also be told to fail."""

    def __init__(self, *, failing: bool = False) -> None:
        self.saved: list[ErrorReport] = []
        self._failing = failing

    async def save(self, report: ErrorReport) -> None:
        if self._failing:
            msg = "the database is down"
            raise RuntimeError(msg)
        self.saved.append(report)

    async def find(self, reference: str, *, now: Instant) -> ErrorReport | None:
        matches = self._matching(reference, now)
        return matches[0] if matches else None

    async def count_matching(self, reference: str, *, now: Instant) -> int:
        return len(self._matching(reference, now))

    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        unexpired = [
            report for report in self.saved if report.expires_at > now and (report.work_lost or not work_lost_only)
        ]
        return sorted(unexpired, key=lambda report: report.occurred_at, reverse=True)[:limit]

    async def purge_expired(self, *, now: Instant) -> int:
        expired = [report for report in self.saved if report.expires_at <= now]
        self.saved = [report for report in self.saved if report.expires_at > now]
        return len(expired)

    async def clear_all(self) -> int:
        cleared = len(self.saved)
        self.saved = []
        return cleared

    def _matching(self, reference: str, now: Instant) -> list[ErrorReport]:
        return [
            report
            for report in self.saved
            if report.expires_at > now and reference in (report.reference, report.correlation_id)
        ]


def build_service(
    repository: FakeRepository,
    *,
    retention_hours: int = 168,
    max_traceback_chars: int = 20000,
) -> ErrorReportService:
    return ErrorReportService(
        repository,
        retention_hours=retention_hours,
        max_traceback_chars=max_traceback_chars,
        now=lambda: FROZEN,
    )


def _throw(error: Exception) -> None:
    raise error


def raised(error: Exception) -> Exception:
    """Return `error` carrying a real traceback, as a captured failure always would."""
    try:
        _throw(error)
    except Exception as caught:
        return caught
    msg = "the helper must have raised"
    raise AssertionError(msg)


async def test_record_captures_the_traceback_and_classification() -> None:
    repository = FakeRepository()
    service = build_service(repository)

    await service.record(
        raised(RecordNotFoundError(record_id=7)),
        correlation_id="a" * 32,
        reference="a" * 12,
        surface="application_command",
        origin="records lookup",
        context={"channel_id": 4},
        log_tail=("first", "second"),
    )

    (report,) = repository.saved
    assert report.correlation_id == "a" * 32
    assert report.reference == "a" * 12
    assert report.exception_type == "RecordNotFoundError"
    assert report.error_code == ErrorCode.RECORD_NOT_FOUND
    assert report.origin == "records lookup"
    assert report.context == {"channel_id": 4}
    assert report.log_tail == ("first", "second")
    assert "RecordNotFoundError" in report.traceback
    assert report.expires_at == FROZEN.add(hours=168)


async def test_record_swallows_a_storage_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Every caller is an error handler that still owes the user a response.

    A report that cannot be written is a lost diagnostic; one that raises turns a handled error
    into a command that silently does nothing.
    """
    service = build_service(FakeRepository(failing=True))

    with caplog.at_level("ERROR"):
        await service.record(raised(RuntimeError("boom")), correlation_id="c" * 32, reference="c" * 12, surface="http")

    assert "Could not store an error report" in caplog.text


async def test_record_truncates_a_runaway_traceback_from_the_front() -> None:
    """The frames nearest the failure are the ones worth reading."""
    repository = FakeRepository()
    service = build_service(repository, max_traceback_chars=200)

    def recurse(depth: int) -> None:
        if depth == 0:
            msg = "bottom"
            raise RuntimeError(msg)
        recurse(depth - 1)

    try:
        recurse(50)
    except RuntimeError as error:
        await service.record(error, correlation_id="d" * 32, reference="d" * 12, surface="worker")

    (report,) = repository.saved
    assert len(report.traceback) <= 204
    assert report.traceback.startswith("...\n")
    assert "RuntimeError: bottom" in report.traceback


async def test_lookup_resolves_either_width_of_the_reference() -> None:
    repository = FakeRepository()
    service = build_service(repository)
    await service.record(raised(RuntimeError("boom")), correlation_id="e" * 32, reference="e" * 12, surface="http")

    from_card, _ = await service.lookup("e" * 12)
    from_header, _ = await service.lookup("e" * 32)

    assert from_card.id == from_header.id


async def test_lookup_strips_the_backticks_the_error_card_rendered() -> None:
    repository = FakeRepository()
    service = build_service(repository)
    await service.record(raised(RuntimeError("boom")), correlation_id="f" * 32, reference="f" * 12, surface="http")

    report, _ = await service.lookup(f"  `{'f' * 12}` ")

    assert report.reference == "f" * 12


async def test_lookup_reports_how_many_share_a_reference() -> None:
    """A 48-bit prefix is not a key, and a moderator may be reading the wrong traceback."""
    repository = FakeRepository()
    service = build_service(repository)
    for correlation in ("1" * 12 + "a" * 20, "1" * 12 + "b" * 20):
        await service.record(
            raised(RuntimeError("boom")), correlation_id=correlation, reference="1" * 12, surface="http"
        )

    _, matches = await service.lookup("1" * 12)

    assert matches == 2


async def test_lookup_rejects_an_unknown_or_expired_reference() -> None:
    service = build_service(FakeRepository())

    with pytest.raises(ErrorReportNotFoundError):
        await service.lookup("0" * 12)


async def test_lookup_rejects_an_empty_reference() -> None:
    service = build_service(FakeRepository())

    with pytest.raises(ErrorReportNotFoundError):
        await service.lookup("  ``  ")


async def test_clear_all_deletes_every_report_expired_or_not() -> None:
    repository = FakeRepository()
    service = build_service(repository)
    await service.record(raised(RuntimeError("boom")), correlation_id="a" * 32, reference="a" * 12, surface="http")
    await service.record(raised(RuntimeError("boom")), correlation_id="b" * 32, reference="b" * 12, surface="http")

    deleted = await service.clear_all()

    assert deleted == 2
    assert repository.saved == []


async def test_retention_must_be_at_least_an_hour() -> None:
    with pytest.raises(InvalidStateError, match="at least one hour"):
        ErrorReportService(FakeRepository(), retention_hours=0)
