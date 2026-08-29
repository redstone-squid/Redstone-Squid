"""Integration coverage for storing error reports and resolving quoted references."""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import ErrorCode
from squid.diagnostics.domain import ErrorReport
from squid.diagnostics.infrastructure.repository import PostgresErrorReportRepository
from squid.persistence.base import Base

_TABLES = [Base.metadata.tables["error_reports"]]

NOW = Instant.from_utc(2026, 8, 17, 12, 0, 0)


@pytest.fixture
async def error_report_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.fixture
def repository(
    error_report_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> PostgresErrorReportRepository:
    return PostgresErrorReportRepository(async_session_factory)


def make_report(
    *,
    correlation_id: str,
    occurred_at: Instant = NOW,
    expires_at: Instant | None = None,
    error_code: ErrorCode | None = ErrorCode.INTERNAL_ERROR,
) -> ErrorReport:
    return ErrorReport(
        id=uuid4(),
        correlation_id=correlation_id,
        reference=correlation_id[:12],
        occurred_at=occurred_at,
        expires_at=expires_at if expires_at is not None else occurred_at.add(hours=168),
        surface="application_command",
        origin="records lookup",
        exception_type="RuntimeError",
        message="boom",
        error_code=error_code,
        traceback="Traceback (most recent call last):\n  RuntimeError: boom\n",
        context={"channel_id": 4, "nested": {"ok": True}},
        log_tail=["first", "second"],
    )


async def test_round_trips_every_field(repository: PostgresErrorReportRepository) -> None:
    stored = make_report(correlation_id="a" * 32)

    await repository.save(stored)
    found = await repository.find("a" * 12, now=NOW)

    assert found is not None
    assert found.id == stored.id
    assert found.correlation_id == "a" * 32
    assert found.reference == "a" * 12
    assert found.surface == "application_command"
    assert found.origin == "records lookup"
    assert found.error_code is ErrorCode.INTERNAL_ERROR
    assert found.context == {"channel_id": 4, "nested": {"ok": True}}
    assert list(found.log_tail) == ["first", "second"]
    assert found.occurred_at == NOW


async def test_resolves_the_short_reference_and_the_full_correlation_id(
    repository: PostgresErrorReportRepository,
) -> None:
    """A moderator quotes what Discord showed; an operator quotes a Request-Id header."""
    await repository.save(make_report(correlation_id="b" * 32))

    from_card = await repository.find("b" * 12, now=NOW)
    from_header = await repository.find("b" * 32, now=NOW)

    assert from_card is not None
    assert from_header is not None
    assert from_card.id == from_header.id


async def test_find_returns_the_newest_of_several_sharing_a_reference(
    repository: PostgresErrorReportRepository,
) -> None:
    older = make_report(correlation_id="c" * 12 + "1" * 20, occurred_at=NOW.subtract(hours=2))
    newer = make_report(correlation_id="c" * 12 + "2" * 20, occurred_at=NOW.subtract(hours=1))
    await repository.save(older)
    await repository.save(newer)

    found = await repository.find("c" * 12, now=NOW)

    assert found is not None
    assert found.id == newer.id
    assert await repository.count_matching("c" * 12, now=NOW) == 2


async def test_expired_reports_are_invisible_and_purgeable(repository: PostgresErrorReportRepository) -> None:
    expired = make_report(correlation_id="d" * 32, expires_at=NOW.subtract(minutes=1))
    live = make_report(correlation_id="e" * 32, expires_at=NOW.add(hours=1))
    await repository.save(expired)
    await repository.save(live)

    assert await repository.find("d" * 12, now=NOW) is None
    assert await repository.purge_expired(now=NOW) == 1
    assert await repository.find("e" * 12, now=NOW) is not None


async def test_list_recent_is_newest_first_and_bounded(repository: PostgresErrorReportRepository) -> None:
    for index in range(3):
        await repository.save(
            make_report(correlation_id=str(index) * 32, occurred_at=NOW.subtract(hours=index)),
        )

    recent = await repository.list_recent(limit=2, now=NOW)

    assert [report.correlation_id for report in recent] == ["0" * 32, "1" * 32]


async def test_a_report_with_no_error_code_round_trips(repository: PostgresErrorReportRepository) -> None:
    """Unexpected failures are the common case and carry no application code."""
    await repository.save(make_report(correlation_id="f" * 32, error_code=None))

    found = await repository.find("f" * 12, now=NOW)

    assert found is not None
    assert found.error_code is None
