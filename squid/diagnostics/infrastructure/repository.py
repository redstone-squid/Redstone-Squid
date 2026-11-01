"""PostgreSQL error report repository."""

from collections.abc import Sequence
from typing import cast, override

from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.diagnostics.application import ErrorReportRepository
from squid.diagnostics.models import ErrorReport


class PostgresErrorReportRepository(ErrorReportRepository):
    """Store error reports and resolve either width of the reference a user quotes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def save(self, report: ErrorReport) -> None:
        async with self._session_factory.begin() as session:
            session.add(report)

    @override
    async def find(self, reference: str, *, now: Instant) -> ErrorReport | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ErrorReport)
                .where(_matches(reference), ErrorReport.expires_at > now)
                .order_by(ErrorReport.occurred_at.desc())
                .limit(1)
            )

    @override
    async def count_matching(self, reference: str, *, now: Instant) -> int:
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(ErrorReport).where(_matches(reference), ErrorReport.expires_at > now)
            )
        return total or 0

    @override
    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ErrorReport)
                .where(ErrorReport.expires_at > now)
                .where(ErrorReport.work_lost if work_lost_only else true())
                .order_by(ErrorReport.occurred_at.desc())
                .limit(limit)
            )
            return rows.all()

    @override
    async def purge_expired(self, *, now: Instant) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(delete(ErrorReport).where(ErrorReport.expires_at <= now))
        return cast(CursorResult[tuple[()]], result).rowcount

    @override
    async def clear_all(self) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(delete(ErrorReport))
        return cast(CursorResult[tuple[()]], result).rowcount


def _matches(reference: str):
    """Match a quoted reference against either width it could have been read from.

    A user reads the short form off a Discord card; an operator reads the full ID off a
    `Request-Id` header or a log line. Both must resolve, and both are exact matches on an indexed
    column rather than a prefix scan.
    """
    return or_(ErrorReport.reference == reference, ErrorReport.correlation_id == reference)
