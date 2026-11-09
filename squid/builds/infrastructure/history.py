"""Capture persisted build aggregates in the same transaction as their changes."""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from squid.builds.history import BuildChange, BuildHistoryEntry, BuildHistorySummary, BuildSnapshot, HistoryOperation
from squid.builds.history_models import BuildHistoryRecord, BuildHistoryState
from squid.core.pagination import FIRST_PAGE, MAX_PAGE_OFFSET, Page, PageSelector, keyset_page

_SCOPE = "build_history_operation"


async def _snapshot(session: AsyncSession, build_id: int) -> BuildSnapshot | None:
    row = (
        await session.execute(
            text("SELECT revision, public.build_history_snapshot_v1(id) AS snapshot FROM builds WHERE id = :id"),
            {"id": build_id},
        )
    ).one_or_none()
    return None if row is None else BuildSnapshot(schema_version=1, revision=row.revision, payload=row.snapshot)


def _state(snapshot: BuildSnapshot, build_id: int) -> BuildHistoryState:
    return BuildHistoryState(
        build_id=build_id,
        build_revision=snapshot.revision,
        schema_version=snapshot.schema_version,
        snapshot=snapshot.payload,
    )


@dataclass
class BuildHistoryOperation:
    """One logical operation; nested repositories share its first observed states."""

    session: AsyncSession
    change: BuildChange
    before: dict[int, BuildSnapshot | None] = field(default_factory=dict)
    failed: bool = False

    async def watch(self, build_ids: Sequence[int]) -> None:
        """Lock existing builds before reading; multi-build callers supply the whole set."""
        with self.session.no_autoflush:
            for build_id in sorted(set(build_ids) - self.before.keys()):
                await self.session.execute(text("SELECT id FROM builds WHERE id = :id FOR UPDATE"), {"id": build_id})
                self.before[build_id] = await _snapshot(self.session, build_id)

    def created(self, build_id: int) -> None:
        self.before[build_id] = None

    async def finish(self) -> None:
        if self.failed:
            msg = "Cannot record a build operation after a nested write failed"
            raise RuntimeError(msg)
        await self.session.flush()
        for build_id, before in sorted(self.before.items()):
            after = await _snapshot(self.session, build_id)
            if before is None and after is None:
                continue
            if before is not None and after is not None and before.payload == after.payload:
                continue
            previous_id: int | None = None
            if before is not None:
                previous = await self.session.scalar(
                    select(BuildHistoryState)
                    .join(BuildHistoryRecord, BuildHistoryRecord.after_state_id == BuildHistoryState.id)
                    .where(BuildHistoryRecord.build_id == build_id)
                    .order_by(BuildHistoryRecord.id.desc())
                    .limit(1)
                )
                if previous is not None and (previous.snapshot, previous.build_revision, previous.schema_version) == (
                    before.payload,
                    before.revision,
                    before.schema_version,
                ):
                    previous_id = previous.id
                else:
                    previous = _state(before, build_id)
                    self.session.add(previous)
                    await self.session.flush()
                    previous_id = previous.id
            following = _state(after, build_id) if after is not None else None
            if following is not None:
                self.session.add(following)
                await self.session.flush()
            self.session.add(
                BuildHistoryRecord(
                    build_id=build_id,
                    operation_id=self.change.operation_id,
                    operation="create" if before is None else "delete" if after is None else self.change.operation,
                    source=self.change.source,
                    actor_account_id=self.change.actor_account_id,
                    actor_system=self.change.actor_system,
                    correlation_kind=self.change.correlation_kind,
                    correlation_id=self.change.correlation_id,
                    before_state_id=previous_id,
                    after_state_id=following.id if following is not None else None,
                )
            )
        await self.session.flush()


@asynccontextmanager
async def record_build_changes(
    session: AsyncSession, change: BuildChange, build_ids: Sequence[int] = ()
) -> AsyncIterator[BuildHistoryOperation]:
    """Join an outer operation or record once when the outermost scope exits.

    The caller owns the database transaction. Exceptions must abort that transaction;
    this scope never commits. Attribution belongs to the outermost operation.
    """
    active = cast(BuildHistoryOperation | None, session.info.get(_SCOPE))
    if active is not None:
        try:
            await active.watch(build_ids)
            yield active
        except BaseException:
            active.failed = True
            raise
        return
    operation = BuildHistoryOperation(session, change)
    session.info[_SCOPE] = operation
    try:
        await operation.watch(build_ids)
        yield operation
        await operation.finish()
    finally:
        session.info.pop(_SCOPE, None)


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
        before = aliased(BuildHistoryState)
        after = aliased(BuildHistoryState)
        statement = (
            select(BuildHistoryRecord, before.build_revision, after.build_revision)
            .outerjoin(before, before.id == BuildHistoryRecord.before_state_id)
            .outerjoin(after, after.id == BuildHistoryRecord.after_state_id)
            .where(BuildHistoryRecord.build_id == build_id)
        )
        if selector.after_id is not None:
            statement = statement.where(BuildHistoryRecord.id < selector.after_id)
        if selector.before_id is not None:
            statement = statement.where(BuildHistoryRecord.id > selector.before_id)
        statement = (
            statement.order_by(
                BuildHistoryRecord.id.asc() if selector.before_id is not None else BuildHistoryRecord.id.desc()
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
            select(func.count()).select_from(BuildHistoryRecord).where(BuildHistoryRecord.build_id == build_id)
        )
        return keyset_page(
            items, selector=selector, page_size=page_size, total=total or 0, keyset=True, id_of=lambda item: item.id
        )

    async def get_history_entry(self, build_id: int, entry_id: int) -> BuildHistoryEntry | None:
        session = self._session()
        record = await session.scalar(
            select(BuildHistoryRecord).where(BuildHistoryRecord.id == entry_id, BuildHistoryRecord.build_id == build_id)
        )
        if record is None:
            return None
        before = await session.get(BuildHistoryState, record.before_state_id) if record.before_state_id else None
        after = await session.get(BuildHistoryState, record.after_state_id) if record.after_state_id else None
        summary = _summary(record, before.build_revision if before else None, after.build_revision if after else None)
        return BuildHistoryEntry(
            id=summary.id,
            build_id=summary.build_id,
            recorded_at=summary.recorded_at,
            change=summary.change,
            before_revision=summary.before_revision,
            after_revision=summary.after_revision,
            before=_decode(before),
            after=_decode(after),
        )


def _decode(state: BuildHistoryState | None) -> BuildSnapshot | None:
    return (
        None
        if state is None
        else BuildSnapshot(
            schema_version=state.schema_version, revision=state.build_revision, payload=deepcopy(state.snapshot)
        )
    )


def _summary(record: BuildHistoryRecord, before: int | None, after: int | None) -> BuildHistorySummary:
    return BuildHistorySummary(
        id=record.id,
        build_id=record.build_id,
        recorded_at=record.recorded_at,
        before_revision=before,
        after_revision=after,
        change=BuildChange(
            operation=cast(HistoryOperation, record.operation),
            source=record.source,
            actor_account_id=record.actor_account_id,
            actor_system=record.actor_system,
            correlation_kind=record.correlation_kind,
            correlation_id=record.correlation_id,
            operation_id=record.operation_id,
        ),
    )
