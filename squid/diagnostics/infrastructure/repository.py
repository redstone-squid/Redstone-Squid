"""PostgreSQL error report repository."""

from collections.abc import Sequence
from typing import cast, override

from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import ErrorCode, JSONValue
from squid.diagnostics.application import ErrorReportRepository
from squid.diagnostics.domain import ErrorReport
from squid.diagnostics.infrastructure.models import ErrorReport as ErrorReportRow


class PostgresErrorReportRepository(ErrorReportRepository):
    """Store error reports and resolve either width of the reference a user quotes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def save(self, report: ErrorReport) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                ErrorReportRow(
                    id=report.id,
                    correlation_id=report.correlation_id,
                    reference=report.reference,
                    occurred_at=report.occurred_at,
                    expires_at=report.expires_at,
                    surface=report.surface,
                    origin=report.origin,
                    exception_type=report.exception_type,
                    message=report.message,
                    error_code=report.error_code.value if report.error_code is not None else None,
                    traceback=report.traceback,
                    context=dict(report.context),
                    log_tail=list(report.log_tail),
                    work_lost=report.work_lost,
                )
            )

    @override
    async def find(self, reference: str, *, now: Instant) -> ErrorReport | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ErrorReportRow)
                .where(_matches(reference), ErrorReportRow.expires_at > now)
                .order_by(ErrorReportRow.occurred_at.desc())
                .limit(1)
            )
        return _to_domain(row) if row is not None else None

    @override
    async def count_matching(self, reference: str, *, now: Instant) -> int:
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ErrorReportRow)
                .where(_matches(reference), ErrorReportRow.expires_at > now)
            )
        return total or 0

    @override
    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ErrorReportRow)
                .where(ErrorReportRow.expires_at > now)
                .where(ErrorReportRow.work_lost if work_lost_only else true())
                .order_by(ErrorReportRow.occurred_at.desc())
                .limit(limit)
            )
            return [_to_domain(row) for row in rows]

    @override
    async def purge_expired(self, *, now: Instant) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(delete(ErrorReportRow).where(ErrorReportRow.expires_at <= now))
        return cast(CursorResult[tuple[()]], result).rowcount

    @override
    async def clear_all(self) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(delete(ErrorReportRow))
        return cast(CursorResult[tuple[()]], result).rowcount


def _matches(reference: str):
    """Match a quoted reference against either width it could have been read from.

    A user reads the short form off a Discord card; an operator reads the full ID off a
    `Request-Id` header or a log line. Both must resolve, and both are exact matches on an indexed
    column rather than a prefix scan.
    """
    return or_(ErrorReportRow.reference == reference, ErrorReportRow.correlation_id == reference)


def _error_code(stored: str | None) -> ErrorCode | None:
    """Read back a stored code, tolerating one this build no longer defines.

    Reports outlive deployments. A code renamed or dropped since a report was written must not
    make that report unreadable -- losing the classification is survivable, losing the traceback
    to a ValueError is not.
    """
    if stored is None:
        return None
    try:
        return ErrorCode(stored)
    except ValueError:
        return None


def _to_domain(row: ErrorReportRow) -> ErrorReport:
    return ErrorReport(
        id=row.id,
        correlation_id=row.correlation_id,
        reference=row.reference,
        occurred_at=row.occurred_at,
        expires_at=row.expires_at,
        surface=row.surface,
        origin=row.origin,
        exception_type=row.exception_type,
        message=row.message,
        error_code=_error_code(row.error_code),
        traceback=row.traceback,
        context=cast(dict[str, JSONValue], row.context),
        log_tail=list(row.log_tail),
        work_lost=row.work_lost,
    )
