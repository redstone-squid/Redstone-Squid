"""PostgreSQL error report repository."""

from collections.abc import Callable, Sequence
from typing import Any, cast, override
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.diagnostics.application import ErrorReportRepository
from squid.core.diagnostics.models import ErrorReport
from squid.core.pagination import PageSelector
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
        self, *, selector: PageSelector[UUID], limit: int, now: Instant, work_lost_only: bool = False
    ) -> Sequence[ErrorReport]:
        """Read reports newest first; a `before_id` page is read in reverse and restored to that order."""
        session = self._session()
        order = KeysetOrder(ErrorReport.occurred_at, ErrorReport.id, descending=True)
        listing = _unexpired(select(ErrorReport), now, work_lost_only=work_lost_only)
        rows = (await session.scalars((await order.apply(session, listing, selector)).limit(limit))).all()
        return rows[::-1] if selector.before_id is not None else rows

    @override
    async def count_recent(self, *, now: Instant, work_lost_only: bool = False) -> int:
        statement = _unexpired(select(func.count()).select_from(ErrorReport), now, work_lost_only=work_lost_only)
        return await self._session().scalar(statement) or 0

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


def _unexpired[S: Select[Any]](statement: S, now: Instant, *, work_lost_only: bool) -> S:
    """Keep reports still retained at `now`, optionally only those that abandoned work."""
    return statement.where(ErrorReport.expires_at > now, ErrorReport.work_lost if work_lost_only else true())
