"""The current version is configuration, not an inference from publication order."""

from collections.abc import Sequence
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, analyze_door_widths
from squid.records.application.models import CandidateBuild, ComputationBatch
from squid.records.application.range_publication import RangePublicationService
from squid.records.application.services import RecordComputationService
from squid.records.domain import BuildKind
from squid.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from squid.records.models import DoorRangeRecomputeQueueItem, RecordRecomputeQueueItem, RecordSettings
from squid.versions.models import Version


async def test_pin_invalidates_both_kinds_and_is_not_changed_by_fixed_publications(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with sessions.begin() as session:
        current = Version(edition="Java", major_version=1, minor_version=21, patch_number=0)
        other = Version(edition="Java", major_version=1, minor_version=20, patch_number=0)
        session.add_all((current, other))
        await session.flush()
        current_id, other_id = current.id, other.id

    async with factory() as uow:
        revision = await uow.records.active_rules_revision_id()
        query_id = await uow.record_ranges.register(DoorRangeQuery(height=3))
        await uow.records.set_current_version(current_id)
        await uow.commit()
    async with sessions() as session:
        settings = await session.get(RecordSettings, 1)
        assert settings is not None
        assert settings.current_version_id == current_id
        assert settings.revision == 1
        kinds = set((await session.scalars(select(RecordRecomputeQueueItem.build_kind))).all())
        assert kinds == {BuildKind.DOOR, BuildKind.EXTENDER}
        assert await session.get(DoorRangeRecomputeQueueItem, query_id) is not None

    async with factory() as uow:
        lease = await uow.records.claim_recompute_kinds(limit=10)
        await uow.commit()
    async with factory() as uow:
        await uow.records.set_current_version(current_id)
        await uow.records.activate(ComputationBatch(revision, BuildKind.DOOR, other_id, ()))
        await uow.commit()
    async with factory() as uow:
        assert await uow.records.active_current_version_id() == current_id
        await uow.records.complete_recompute(lease)
        await uow.commit()
    async with sessions() as session:
        assert (await session.scalars(select(RecordRecomputeQueueItem))).all() == []
        settings = await session.get(RecordSettings, 1)
        assert settings is not None
        assert settings.revision == 1


async def test_current_ranges_follow_the_pin_without_changing_fixed_queries(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with sessions.begin() as session:
        first = Version(edition="Java", major_version=1, minor_version=21, patch_number=0)
        second = Version(edition="Java", major_version=1, minor_version=22, patch_number=0)
        session.add_all((first, second))
        await session.flush()
        first_id, second_id = first.id, second.id
    async with factory() as uow:
        await uow.records.set_current_version(first_id)
        await uow.commit()

    class PinChangingAnalyzer:
        changed = False

        async def analyze(self, sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
            if query.follow_current_version and not self.changed:
                async with factory() as uow:
                    await uow.records.set_current_version(second_id)
                    await uow.commit()
                self.changed = True
            return analyze_door_widths(sources, height=query.height)

    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=PinChangingAnalyzer()
    )
    fixed = await publisher.publish(DoorRangeQuery(height=3, version_id=first_id))
    current = await publisher.publish(DoorRangeQuery(height=3, follow_current_version=True))
    assert current.query_id != fixed.query_id
    assert current.active is None
    assert current.latest is not None
    assert current.latest.resolved_version_id == first_id
    assert fixed.active is not None
    assert fixed.active.resolved_version_id == first_id

    await publisher.process_queue()

    current = await publisher.get(current.query_id)
    fixed = await publisher.get(fixed.query_id)
    assert current is not None
    assert current.active is not None
    assert current.active.resolved_version_id == second_id
    assert fixed is not None
    assert fixed.active is not None
    assert fixed.active.resolved_version_id == first_id


async def test_current_range_without_a_pin_stays_pending(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    publisher = RangePublicationService(
        RecordComputationService(uow_factory=factory), uow_factory=factory, analyzer=SubprocessRangeAnalyzer()
    )
    query_id = await publisher.register(DoorRangeQuery(height=3, follow_current_version=True))
    assert await publisher.process_queue() == (0, 1)
    publication = await publisher.get(query_id)
    assert publication is not None
    assert publication.active is None
    assert publication.latest is not None
    assert publication.latest.error == "current_version_unset"
