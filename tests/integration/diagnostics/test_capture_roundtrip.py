"""A failure captured by a real handler must be readable through the lookup surface.

The unit tests prove capture is called and prove lookup renders, each against a fake of the
other half. This is the seam between them: the reference a caller is handed has to be the key
the row is actually filed under, through real serialization and a real database.
"""

import logging
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.core.errors import InternalError
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.domain import ErrorReportNotFoundError
from squid.diagnostics.infrastructure.repository import PostgresErrorReportRepository
from squid.observability import (
    CorrelatedLogBuffer,
    bind_correlation_id,
    correlation_id,
    correlation_reference,
    unbind_correlation_id,
)
from squid.persistence.base import Base

_TABLES = [Base.metadata.tables["error_reports"]]


@pytest.fixture
async def service(
    async_engine: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[ErrorReportService]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield ErrorReportService(PostgresErrorReportRepository(async_session_factory))
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


def _fail() -> None:
    """Raise from a named frame, so the stored traceback has one worth reading."""
    msg = "the connection string is postgres://secret"
    raise InternalError(msg, context={"job_id": 17})


async def test_a_captured_failure_is_readable_by_the_reference_the_user_was_shown(
    service: ErrorReportService,
) -> None:
    token = bind_correlation_id("a1b2c3d4e5f6" + "9" * 20)
    try:
        full = correlation_id()
        shown = correlation_reference(full)
        try:
            _fail()
        except InternalError as error:
            await service.record(
                error,
                correlation_id=full,
                reference=shown,
                surface="application_command",
                origin="records lookup",
                context={"job_id": 17},
                log_tail=("fetching record 17",),
            )
    finally:
        unbind_correlation_id(token)

    from_card, matches = await service.lookup(f"`{shown}`")
    from_header, _ = await service.lookup(full)

    assert from_card.id == from_header.id
    assert matches == 1
    assert from_card.exception_type == "InternalError"
    assert "postgres://secret" in from_card.message
    assert "InternalError" in from_card.traceback
    assert from_card.context == {"job_id": 17}
    assert list(from_card.log_tail) == ["fetching record 17"]


async def test_the_log_buffer_supplies_the_tail_a_report_stores(service: ErrorReportService) -> None:
    """End to end through the real handler: bound ID, buffered lines, drained into the row."""
    buffer = CorrelatedLogBuffer(max_records=10)
    buffer.setFormatter(logging.Formatter("%(message)s"))
    correlation = "b1b2c3d4e5f6" + "8" * 20

    for step in ("opened the schematic", "resolved the version"):
        record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, step, (), None)
        record.request_id = correlation
        buffer.handle(record)

    msg = "boom"
    error = RuntimeError(msg)
    await service.record(
        error,
        correlation_id=correlation,
        reference=correlation_reference(correlation),
        surface="background_job",
        origin="schematic-jobs",
        log_tail=buffer.drain(correlation),
    )

    report, _ = await service.lookup("b1b2c3d4e5f6")

    assert list(report.log_tail) == ["opened the schematic", "resolved the version"]
    assert report.origin == "schematic-jobs"


async def test_a_reference_outside_the_retention_window_is_indistinguishable_from_a_typo(
    service: ErrorReportService,
) -> None:
    """Saying a reference *used* to exist would reveal that an error happened."""
    with pytest.raises(ErrorReportNotFoundError):
        await service.lookup("ffffffffffff")
