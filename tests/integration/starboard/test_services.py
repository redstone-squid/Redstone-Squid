"""Starboard transaction and tracked-setting behavior on the deployed schema."""

from functools import partial

import pytest
from anyio import create_task_group
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.reactions.domain import ReactionActor
from squid.starboard.application import PendingVote, StarboardService
from squid.starboard.models import OriginMessage


async def test_setting_changes_validate_before_commit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with factory() as uow:
        await uow.settings.get_or_create_for_update(10)
        await uow.commit()
    service = StarboardService(uow_factory=factory)
    created = await service.create_starboard(10, 20)
    updated = await service.update_settings(10, "main", channel_id=21, required=5.0)
    assert updated is not None
    assert updated.id == created.id
    assert updated.channel_id == 21
    with pytest.raises(ValueError, match="threshold"):
        await service.update_settings(10, "main", required=-1.0)
    stored = await service.get(10, "main")
    assert stored is not None
    assert stored.required == 5.0


async def test_concurrent_votes_commit_and_abandoned_votes_leave_no_entry(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with factory() as uow:
        await uow.settings.get_or_create_for_update(10)
        await uow.commit()
    service = StarboardService(uow_factory=factory)
    config = await service.create_starboard(10, 20, required=1.0)
    origin = OriginMessage(
        id=100,
        guild_id=10,
        channel_id=30,
        author_id=40,
        author_is_bot=False,
        posted_at=Instant.now().subtract(seconds=60),
    )
    async with factory() as uow:
        repository = uow.starboards
        await repository.record_votes(origin, 50, [PendingVote(config, "⭐", "up", 1.0)])
        assert await uow.starboards.entry_state(config.id, origin.id) is not None
    assert await service.entry_state(config.id, origin.id) is None
    with pytest.raises(InvalidStateError):
        await repository.record_votes(origin, 50, ())

    async with create_task_group() as tasks:
        tasks.start_soon(service.record_vote, origin, ReactionActor(50, 10), "⭐")
        tasks.start_soon(service.record_vote, origin, ReactionActor(51, 10), "⭐")
    stored = await service.entry_state(config.id, origin.id)
    assert stored is not None
    assert stored.entry.score == 2.0
    assert stored.entry.raw_count == 2
