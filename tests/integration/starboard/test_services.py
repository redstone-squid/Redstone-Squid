"""Starboard transaction and tracked-setting behavior on the deployed schema."""

from functools import partial

import pytest
from anyio import create_task_group
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.messages.models import Message
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
        author_is_bot=False,
        message=Message(
            id=100, guild_id=10, channel_id=30, author_id=40, created_at=Instant.now().subtract(seconds=60)
        ),
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


async def test_shared_deletion_survives_reactions_and_recounts(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = StarboardService(uow_factory=factory)
    config = await service.create_starboard(10, 20, required=1.0)
    origin = OriginMessage(
        id=100,
        author_is_bot=False,
        message=Message(
            id=100, guild_id=10, channel_id=30, author_id=40, created_at=Instant.now().subtract(seconds=60)
        ),
    )
    vote = PendingVote(config, "⭐", "up", 1.0)
    async with factory() as uow:
        await uow.starboards.record_votes(origin, 50, [vote])
        await uow.commit()
    deleted_at = Instant.from_utc(2026, 11, 9)
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE messages SET content = 'retained' WHERE id = 100"))
        await session.execute(text("DELETE FROM discord_sync_queue"))
    async with factory() as uow:
        # This is the shared message handler's path, without a starboard-specific deletion call.
        await uow.messages.mark_deleted(origin.id, deleted_at)
        await uow.commit()
    async with migrated_session_factory() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM discord_sync_queue WHERE resource_kind = 'starboard_entry'")
            )
            == 1
        )
    for recount in (False, True):
        async with factory() as uow:
            if recount:
                await uow.starboards.recount_votes(origin, [(50, vote)])
            else:
                await uow.starboards.record_votes(origin, 50, [vote])
            await uow.commit()
        state = await service.entry_state(config.id, origin.id)
        assert state is not None
        assert not state.origin.present
        assert state.origin.deleted_at == deleted_at
        assert state.origin.message.content == "retained"
    async with factory() as uow:
        before = await uow.starboards.entry_state(config.id, origin.id)
        assert before is not None
        assert not before.origin.has_image
        origin.has_image = True
        await uow.starboards.recount_votes(origin, [(50, vote)])
        after = await uow.starboards.entry_state(config.id, origin.id)
        assert after is not None
        assert after.origin.has_image
        assert not after.origin.present
        await uow.commit()


async def test_configuration_history_rejects_stale_values_and_restores_unused_board(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = StarboardService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    board = await service.create_starboard(10, 20)
    original = await service.snapshot(board.id)
    assert original is not None
    await service.update_settings(10, "main", required=5.0)
    changed = await service.snapshot(board.id)
    assert changed is not None
    assert not await service.replace_snapshot(original, None)
    assert await service.replace_snapshot(changed, original)
    assert await service.replace_snapshot(original, None)
    assert await service.snapshot(board.id) is None
    assert await service.replace_snapshot(None, original)
    assert await service.snapshot(board.id) == original
