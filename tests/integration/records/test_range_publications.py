"""Range publication preserves old evidence and fences out-of-order workers."""

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial
from unittest.mock import create_autospec

import anyio
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.models import DoorBuild
from squid.core.errors import InvalidStateError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, analyze_door_widths
from squid.records.application.models import CandidateBuild
from squid.records.application.ports import DoorRangeAnalyzer
from squid.records.application.range_publication import RangePublicationService
from squid.records.application.range_snapshots import RangeSnapshotDocument, RangeSource
from squid.records.application.services import RecordComputationService
from squid.records.domain.regions import RegionAnalysisStatus
from squid.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from squid.records.infrastructure.range_wire import failed_analysis
from squid.records.models import DoorRangeRecomputeQueueItem, DoorRangeSnapshot, RulesRevision
from tests.support.build_edits import build_edit_session
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
    # Changed source evidence and results become visible in one commit.
    replacement_sources = (family(branch(volume="20n")),)
    replacement_document = document.model_copy(
        update={"sources": tuple(RangeSource.from_candidate(s) for s in replacement_sources)}
    )
    replacement_result = analyze_door_widths(replacement_sources, height=3)
    async with factory() as uow:
        replacement = await uow.record_ranges.begin(query_id, revision)
        await uow.record_ranges.save_snapshot(replacement.id, replacement_document)
        await uow.commit()
    async with factory() as writer:
        await writer.record_ranges.finish(replacement.id, replacement_result)
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
        assert published.active.result == replacement_result
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


async def test_publication_uses_saved_inputs_despite_live_edits_and_queue_refreshes_them(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        door = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            expansion=family(branch(volume="10n")).expansion,
        )
        session.add(door)
        await session.flush()
        build_id = door.id
        fixed = DoorBuild(
            submitter_account_id=account.id,
            submission_status=Status.CONFIRMED,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=10,
            height=10,
            depth=1,
        )
        session.add(fixed)
        await session.flush()
        fixed_id = fixed.id

    class EditingAnalyzer:
        edited = False

        async def analyze(self, sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
            # The saved snapshot is visible before computation starts. Editing a
            # source now must only affect the next attempt.
            async with sessions() as session:
                count = (await session.execute(select(func.count()).select_from(DoorRangeSnapshot))).scalar_one()
                assert count >= 1
            if not self.edited:
                async with build_edit_session(sessions) as session:
                    door = await session.get(DoorBuild, build_id)
                    assert door is not None
                    door.expansion = family(branch(volume="100n")).expansion
                self.edited = True
            return await SubprocessRangeAnalyzer().analyze(sources, query)

    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=EditingAnalyzer()
    )
    first = await publisher.publish(DoorRangeQuery(height=3))
    assert first.active is not None
    first_run_id = first.active.id
    point = await publisher.get_point(first.query_id, 6)
    assert point is not None
    assert point.region is not None
    assert point.region.resolution.holder_ids == (build_id,)
    assert point.publication is not None
    assert point.publication.id == first_run_id
    async with factory() as uow:
        saved = await uow.record_ranges.get_snapshot(first_run_id)
        assert saved is not None
        assert saved.sources[0].expansion is not None
        assert saved.sources[0].expansion.branches[0].volume == "10n"
    await publisher.process_queue()
    refreshed = await publisher.get(first.query_id)
    assert refreshed is not None
    assert refreshed.active is not None
    assert refreshed.active.id != first_run_id
    point = await publisher.get_point(first.query_id, 6)
    assert point is not None
    assert point.region is not None
    assert point.region.resolution.holder_ids == (fixed_id,)
    assert point.publication is not None
    assert point.publication.id == refreshed.active.id
    async with factory() as uow:
        old = await uow.record_ranges.get_snapshot(first_run_id)
        new = await uow.record_ranges.get_snapshot(refreshed.active.id)
        assert old == saved
        assert new is not None
        assert new.sources[0].expansion is not None
        assert new.sources[0].expansion.branches[0].volume == "100n"


async def test_cancelled_publication_records_failure_and_preserves_previous_result(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    analyzer.analyze.return_value = analyze_door_widths((), height=3)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    query = DoorRangeQuery(height=3)
    first = await publisher.publish(query)
    assert first.active is not None
    started = anyio.Event()

    async def block(sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
        started.set()
        await anyio.sleep_forever()
        raise AssertionError("Cancelled analysis returned")

    analyzer.analyze.side_effect = block
    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(publisher.publish, query)
            await started.wait()
            group.cancel_scope.cancel()
    state = await publisher.get(first.query_id)
    assert state is not None
    assert state.active is not None
    assert state.latest is not None
    assert state.active.id == first.active.id
    assert state.latest.error == "cancelled"
    analyzer.analyze.side_effect = None
    replacement = await publisher.publish(query)
    assert replacement.active is not None
    assert replacement.active.id == first.active.id
    assert replacement.latest is not None
    assert replacement.latest.id != first.active.id
    assert replacement.latest.error is None


async def test_new_rules_revision_prevents_old_attempt_activation(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    query = DoorRangeQuery(height=3)
    async with factory() as uow:
        revision = await uow.records.active_rules_revision_id()
        query_id = await uow.record_ranges.register(query)
        attempt = await uow.record_ranges.begin(query_id, revision)
        await uow.record_ranges.save_snapshot(
            attempt.id, RangeSnapshotDocument(query=query, sources=(), captured_at=datetime.now(UTC))
        )
        await uow.commit()
    async with migrated_session_factory() as session:
        await session.execute(update(RulesRevision).where(RulesRevision.id == revision).values(activated_at=None))
        await session.commit()
    async with factory() as uow:
        await uow.record_ranges.finish(attempt.id, analyze_door_widths((), height=3))
        await uow.commit()
    async with factory() as uow:
        state = await uow.record_ranges.get(query_id)
        assert state is not None
        assert state.active is None
        assert state.latest is not None
        assert state.latest.result is not None
        assert state.latest.result.analysis.status is RegionAnalysisStatus.EXACT


async def test_registration_deduplicates_and_refresh_preserves_new_queue_work(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    query = DoorRangeQuery(
        height=3, restriction_ids=frozenset({7, 8}), restriction_values=((7, "exact", "3"), (8, "at_most", "4"))
    )
    query_id = await publisher.register(query)
    async with factory() as uow:
        lease = await uow.record_ranges.claim_refresh()
        assert lease is not None
        await uow.commit()
    reordered = query.model_copy(update={"restriction_values": tuple(reversed(query.restriction_values))})
    assert await publisher.register(reordered) == query_id
    async with migrated_session_factory() as session:
        token = await session.scalar(select(DoorRangeRecomputeQueueItem.claim_token))
        assert token == lease.token
    assert not await publisher.request_refresh(query_id + 999)
    assert await publisher.request_refresh(query_id)
    async with factory() as uow:
        await uow.record_ranges.complete_refresh(lease)
        await uow.commit()
    async with factory() as uow:
        replacement = await uow.record_ranges.claim_refresh()
        assert replacement is not None
        assert replacement.token != lease.token
        await uow.commit()
    analyzer.analyze.assert_not_called()


async def test_refresh_attempts_every_query_before_reporting_a_failure(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    broken = await publisher.register(DoorRangeQuery(height=3))
    healthy = await publisher.register(DoorRangeQuery(height=4))

    async def analyze(sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
        if query.height == 3:
            raise RuntimeError("worker unavailable")
        return analyze_door_widths((), height=query.height)

    analyzer.analyze.side_effect = analyze
    assert await publisher.process_queue() == (1, 1)
    failed = await publisher.get(broken)
    refreshed = await publisher.get(healthy)
    assert failed is not None
    assert failed.active is None
    assert failed.latest is not None
    assert failed.latest.error == "RuntimeError"
    assert refreshed is not None
    assert refreshed.active is not None


async def test_query_with_too_many_sources_saves_no_snapshot(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add_all(
            DoorBuild(
                submitter_account_id=account.id,
                submission_status=Status.CONFIRMED,
                orientation="Door",
                door_width=width,
                door_height=3,
            )
            for width in range(1, 34)
        )
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    publication = await publisher.publish(DoorRangeQuery(height=3))
    assert publication.active is None
    assert publication.latest is not None
    assert publication.latest.error == "too_many_sources"
    analyzer.analyze.assert_not_called()
    async with sessions() as session:
        assert (await session.execute(select(DoorRangeSnapshot))).scalars().all() == []


async def test_inexact_refresh_keeps_publication_and_retries_only_that_query(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    analyzer.analyze.return_value = analyze_door_widths((), height=3)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    query_id = await publisher.register(DoorRangeQuery(height=3))
    assert await publisher.process_queue() == (1, 0)
    first = await publisher.get(query_id)
    assert first is not None
    assert first.active is not None

    analyzer.analyze.return_value = failed_analysis(RegionAnalysisStatus.TIMED_OUT, "timeout")
    await publisher.request_refresh(query_id)
    assert await publisher.process_queue() == (0, 1)

    current = await publisher.get(query_id)
    assert current is not None
    assert current.active is not None
    assert current.active.id == first.active.id
    async with migrated_session_factory() as session:
        pending = await session.get(DoorRangeRecomputeQueueItem, query_id)
        assert pending is not None
        assert pending.attempts == 1
        assert pending.last_error == RegionAnalysisStatus.TIMED_OUT.value
        assert pending.claim_token is None
    assert await publisher.process_queue() == (0, 0)


async def test_unchanged_range_keeps_publication_and_advances_last_checked(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    analyzer.analyze.return_value = analyze_door_widths((), height=3)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=analyzer
    )
    query_id = await publisher.register(DoorRangeQuery(height=3))
    assert await publisher.process_queue() == (1, 0)
    first = await publisher.get(query_id)
    assert first is not None
    assert first.active is not None
    assert first.last_checked_at is not None
    assert not first.refresh_pending

    await publisher.request_refresh(query_id)
    pending = await publisher.get(query_id)
    assert pending is not None
    assert pending.refresh_pending
    assert await publisher.process_queue() == (1, 0)

    checked = await publisher.get(query_id)
    assert checked is not None
    assert checked.active is not None
    assert checked.latest is not None
    assert checked.active.id == first.active.id
    assert checked.active.snapshot_hash == first.active.snapshot_hash
    assert checked.latest.id != checked.active.id
    assert checked.last_checked_at is not None
    assert checked.last_checked_at >= first.last_checked_at
    assert not checked.refresh_pending
