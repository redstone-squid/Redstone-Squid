"""Range publication preserves old evidence and fences out-of-order workers."""

from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import InvalidStateError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.expansion_regions import DoorRangeQuery, analyze_door_widths
from squid.records.application.range_snapshots import RangeSnapshotDocument, RangeSource
from squid.records.domain.regions import RegionAnalysisStatus
from squid.records.infrastructure.range_wire import failed_analysis
from squid.records.models import DoorRangeSnapshot
from tests.unit.records.application.test_expansion_regions import branch, family


async def test_snapshots_are_immutable_and_old_workers_cannot_replace_newer_attempts(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    query = DoorRangeQuery(height=3)
    sources = (family(branch(volume="10n")),)
    document = RangeSnapshotDocument(
        query=query, sources=tuple(RangeSource.from_candidate(s) for s in sources), captured_at=datetime.now(UTC)
    )
    result = analyze_door_widths(sources, height=3)
    async with factory() as uow:
        revision = await uow.records.active_rules_revision_id()
        query_id = await uow.record_ranges.register(query)
        assert await uow.record_ranges.register(query) == query_id
        first = await uow.record_ranges.begin(query_id, revision)
        await uow.record_ranges.save_snapshot(first.id, document)
        await uow.commit()
    async with factory() as uow:
        await uow.record_ranges.finish(first.id, result)
        await uow.commit()
    async with factory() as uow:
        older = await uow.record_ranges.begin(query_id, revision)
        newer = await uow.record_ranges.begin(query_id, revision)
        await uow.record_ranges.save_snapshot(older.id, document)
        await uow.record_ranges.save_snapshot(newer.id, document)
        await uow.commit()
    async with factory() as uow:
        await uow.record_ranges.finish(newer.id, failed_analysis(RegionAnalysisStatus.TIMED_OUT, "timeout"))
        await uow.commit()
    async with factory() as uow:
        await uow.record_ranges.finish(older.id, result)
        await uow.commit()
    async with factory() as uow:
        publication = await uow.record_ranges.get(query_id)
        assert publication is not None
        assert publication.active is not None
        assert publication.latest is not None
        assert publication.active.id == first.id
        assert publication.latest.id == newer.id
        assert publication.latest.result is not None
        assert publication.latest.result.analysis.status is RegionAnalysisStatus.TIMED_OUT
        assert publication.active.snapshot_hash == document.digest()
        assert await uow.record_ranges.get_snapshot(first.id) == document
        with pytest.raises(InvalidStateError):
            await uow.record_ranges.finish(first.id, result)
    for statement in (
        update(DoorRangeSnapshot).where(DoorRangeSnapshot.run_id == first.id).values(content_hash="changed"),
        delete(DoorRangeSnapshot).where(DoorRangeSnapshot.run_id == first.id),
    ):
        async with migrated_session_factory() as session:
            with pytest.raises(IntegrityError, match="immutable"):
                await session.execute(statement)
            await session.rollback()
    # Replacement activation and its results become visible in one commit.
    async with factory() as uow:
        replacement = await uow.record_ranges.begin(query_id, revision)
        await uow.record_ranges.save_snapshot(replacement.id, document)
        await uow.commit()
    async with factory() as writer:
        await writer.record_ranges.finish(replacement.id, result)
        async with factory() as reader:
            before_commit = await reader.record_ranges.get(query_id)
            assert before_commit is not None
            assert before_commit.active is not None
            assert before_commit.active.id == first.id
        await writer.commit()
    async with factory() as reader:
        published = await reader.record_ranges.get(query_id)
        assert published is not None
        assert published.active is not None
        assert published.active.id == replacement.id
        assert published.active.result == result
        snapshots = await reader.record_ranges.get_snapshot(first.id)
        assert snapshots == document


async def test_failed_attempt_without_snapshot_does_not_publish(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    query = DoorRangeQuery(height=3)
    async with factory() as uow:
        revision = await uow.records.active_rules_revision_id()
        query_id = await uow.record_ranges.register(query)
        attempt = await uow.record_ranges.begin(query_id, revision)
        await uow.commit()
    async with factory() as uow:
        with pytest.raises(InvalidStateError, match="snapshot"):
            await uow.record_ranges.finish(attempt.id, analyze_door_widths((), height=3))
    async with factory() as uow:
        await uow.record_ranges.finish(attempt.id, None, error="worker unavailable")
        await uow.commit()
    async with factory() as uow:
        publication = await uow.record_ranges.get(query_id)
        assert publication is not None
        assert publication.active is None
        assert publication.latest is not None
        assert publication.latest.error == "worker unavailable"
        assert publication.latest.snapshot_hash is None
    async with migrated_session_factory() as session:
        assert (await session.execute(select(DoorRangeSnapshot))).scalars().all() == []
