"""The current version is configuration, not an inference from publication order."""

from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.expansion_regions import DoorRangeQuery
from squid.records.application.models import ComputationBatch
from squid.records.domain import BuildKind
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
