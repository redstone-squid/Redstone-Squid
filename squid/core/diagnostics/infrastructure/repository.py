"""PostgreSQL error report repository."""

from collections.abc import Callable, Sequence
from typing import Any, cast, override

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Date, Instant

from squid.core.diagnostics.application import ErrorReportRepository
from squid.core.diagnostics.domain import EVERY_REPORT, ErrorReportDayCount, ErrorReportFilters
from squid.core.diagnostics.models import ErrorReport
from squid.core.pagination import Page, PageSelector
from squid.core.persistence.paging import KeysetOrder


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
    async def list_recent(
        self, *, selector: PageSelector, page_size: int, now: Instant, filters: ErrorReportFilters = EVERY_REPORT
    ) -> Page[ErrorReport]:
        order = KeysetOrder("newest", ErrorReport.occurred_at, ErrorReport.id, descending=True)
        listing = _unexpired(select(ErrorReport), now, filters)
        total = await self.count_recent(now=now, filters=filters)
        return await order.page(self._session(), listing, selector, page_size=page_size, total=total)

    async def count_recent(self, *, now: Instant, filters: ErrorReportFilters = EVERY_REPORT) -> int:
        statement = _unexpired(select(func.count()).select_from(ErrorReport), now, filters)
        return await self._session().scalar(statement) or 0

    @override
    async def daily_counts(self, *, now: Instant) -> Sequence[ErrorReportDayCount]:
        day = func.date(func.timezone("UTC", ErrorReport.occurred_at)).label("day")
        statement = (
            select(day, ErrorReport.surface, func.count())
            .where(ErrorReport.expires_at > now)
            .group_by(day, ErrorReport.surface)
            .order_by(day, ErrorReport.surface)
        )
        rows = (await self._session().execute(statement)).all()
        return [
            ErrorReportDayCount(day=Date(day_value), surface=surface, count=count) for day_value, surface, count in rows
        ]

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


def _unexpired[S: Select[Any]](statement: S, now: Instant, filters: ErrorReportFilters) -> S:
    """Keep reports still retained at `now` that match every supplied filter."""
    statement = statement.where(ErrorReport.expires_at > now)
    for surface in filters.surfaces:
        statement = statement.where(ErrorReport.surface == surface)
    for error_code in filters.error_codes:
        statement = statement.where(ErrorReport.error_code == error_code)
    if filters.occurred_after is not None:
        statement = statement.where(ErrorReport.occurred_at >= filters.occurred_after)
    if filters.occurred_before is not None:
        statement = statement.where(ErrorReport.occurred_at < filters.occurred_before)
    if filters.work_lost_only:
        statement = statement.where(ErrorReport.work_lost)
    return statement
