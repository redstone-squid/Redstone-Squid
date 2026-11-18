"""PostgreSQL error report repository."""

from collections.abc import Callable, Sequence
from typing import cast, override

from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.diagnostics.application import ErrorReportRepository
from squid.diagnostics.models import ErrorReport


class PostgresErrorReportRepository(ErrorReportRepository):
    """Store reports and look up short references or full correlation IDs.

    The caller owns the transaction, including the commit after writes.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def save(self, report: ErrorReport) -> None:
        session = self._session()
        session.add(report)

    @override
    async def find(self, reference: str, *, now: Instant, prefix: bool = False) -> ErrorReport | None:
        session = self._session()
        return await session.scalar(
            select(ErrorReport)
            .where(_matches(reference, prefix=prefix), ErrorReport.expires_at > now)
            .order_by(ErrorReport.occurred_at.desc())
            .limit(1)
        )

    @override
    async def count_matching(self, reference: str, *, now: Instant, prefix: bool = False) -> int:
        session = self._session()
        total = await session.scalar(
            select(func.count())
            .select_from(ErrorReport)
            .where(_matches(reference, prefix=prefix), ErrorReport.expires_at > now)
        )
        return total or 0

    @override
    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        session = self._session()
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
        session = self._session()
        result = await session.execute(delete(ErrorReport).where(ErrorReport.expires_at <= now))
        return cast(CursorResult[tuple[()]], result).rowcount

    @override
    async def clear_all(self) -> int:
        session = self._session()
        result = await session.execute(delete(ErrorReport))
        return cast(CursorResult[tuple[()]], result).rowcount


def _matches(reference: str, *, prefix: bool):
    correlation_match = (
        ErrorReport.correlation_id.startswith(reference, autoescape=True)
        if prefix
        else ErrorReport.correlation_id == reference
    )
    return or_(ErrorReport.reference == reference, correlation_match)
