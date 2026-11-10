"""Capture persisted build aggregates in the same transaction as their changes."""

from collections.abc import Callable
from copy import deepcopy
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from squid.builds.history import (
    BuildEditContext,
    BuildHistoryDetail,
    BuildHistorySummary,
    BuildStateSnapshot,
    HistoryOperation,
)
from squid.builds.history_models import BuildHistoryEntry, BuildHistorySnapshot
from squid.core.pagination import FIRST_PAGE, MAX_PAGE_OFFSET, Page, PageSelector, keyset_page


class BuildHistoryQueries:
    """Trusted transaction-bound queries; snapshots are loaded only for entry detail."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def list_history(
        self, build_id: int, *, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[BuildHistorySummary]:
        if not 1 <= page_size <= 100:
            msg = "page_size must be between 1 and 100"
            raise ValueError(msg)
        if not 0 <= selector.offset <= MAX_PAGE_OFFSET or (
            sum((bool(selector.offset), selector.after_id is not None, selector.before_id is not None)) > 1
        ):
            msg = "Supply one valid page selector"
            raise ValueError(msg)
        before = aliased(BuildHistorySnapshot)
        after = aliased(BuildHistorySnapshot)
        statement = (
            select(BuildHistoryEntry, before.build_revision, after.build_revision)
            .outerjoin(before, before.id == BuildHistoryEntry.before_state_id)
            .outerjoin(after, after.id == BuildHistoryEntry.after_state_id)
            .where(BuildHistoryEntry.build_id == build_id)
        )
        if selector.after_id is not None:
            statement = statement.where(BuildHistoryEntry.id < selector.after_id)
        if selector.before_id is not None:
            statement = statement.where(BuildHistoryEntry.id > selector.before_id)
        statement = (
            statement.order_by(
                BuildHistoryEntry.id.asc() if selector.before_id is not None else BuildHistoryEntry.id.desc()
            )
            .offset(selector.offset)
            .limit(page_size + 1)
        )
        session = self._session()
        rows = (await session.execute(statement)).all()
        items = [_summary(record, old, new) for record, old, new in rows]
        if selector.before_id is not None:
            items.reverse()
        total = await session.scalar(
            select(func.count()).select_from(BuildHistoryEntry).where(BuildHistoryEntry.build_id == build_id)
        )
        return keyset_page(
            items, selector=selector, page_size=page_size, total=total or 0, keyset=True, id_of=lambda item: item.id
        )

    async def get_history_entry(self, build_id: int, entry_id: int) -> BuildHistoryDetail | None:
        session = self._session()
        record = await session.scalar(
            select(BuildHistoryEntry).where(BuildHistoryEntry.id == entry_id, BuildHistoryEntry.build_id == build_id)
        )
        if record is None:
            return None
        before = await session.get(BuildHistorySnapshot, record.before_state_id) if record.before_state_id else None
        after = await session.get(BuildHistorySnapshot, record.after_state_id) if record.after_state_id else None
        summary = _summary(record, before.build_revision if before else None, after.build_revision if after else None)
        return BuildHistoryDetail(
            id=summary.id,
            build_id=summary.build_id,
            recorded_at=summary.recorded_at,
            context=summary.context,
            before_revision=summary.before_revision,
            after_revision=summary.after_revision,
            before=_decode(before),
            after=_decode(after),
        )


def _decode(state: BuildHistorySnapshot | None) -> BuildStateSnapshot | None:
    return (
        None
        if state is None
        else BuildStateSnapshot(
            schema_version=state.schema_version, revision=state.build_revision, payload=deepcopy(state.snapshot)
        )
    )


def _summary(record: BuildHistoryEntry, before: int | None, after: int | None) -> BuildHistorySummary:
    return BuildHistorySummary(
        id=record.id,
        build_id=record.build_id,
        recorded_at=record.recorded_at,
        before_revision=before,
        after_revision=after,
        context=BuildEditContext(
            operation=cast(HistoryOperation, record.operation),
            source=record.source,
            actor_account_id=record.actor_account_id,
            actor_system=record.actor_system,
            correlation_kind=record.correlation_kind,
            correlation_id=record.correlation_id,
            operation_id=record.operation_id,
        ),
    )
