"""Message transactions against PostgreSQL."""

from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.messages.application.services import MessageService
from squid.messages.domain import MessageSnapshot
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.support.database import assert_loaded_detached


async def test_message_service_commits_updates_and_preserves_timestamps(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = MessageService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    snapshot = MessageSnapshot(id=100, channel_id=20, author_id=30, content="first")
    await service.observe(snapshot)
    first = await service.get(100)
    assert first is not None
    assert_loaded_detached(first)

    await service.observe(MessageSnapshot(id=100, channel_id=20, author_id=30, content="seen again"))
    assert await service.record_edit(100, "edited")
    assert not await service.record_edit(999, "unknown")
    assert await service.mark_deleted(100)
    deleted = await service.get(100)
    assert deleted is not None
    assert deleted.content == "edited"
    assert deleted.observed_at == first.observed_at
    assert deleted.edited_at is not None
    assert deleted.deleted_at is not None

    assert not await service.mark_deleted(100)
    assert not await service.mark_deleted(999)
    again = await service.get(100)
    assert again is not None
    assert again.deleted_at == deleted.deleted_at


async def test_refreshing_fetched_content_changes_only_differing_known_messages(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = MessageService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    # A guild the bot has no settings for yet: observing the message creates them.
    await service.observe(MessageSnapshot(id=100, channel_id=20, author_id=30, guild_id=40, content="first"))
    edited_at = Instant.from_utc(2026, 11, 8, 12)

    assert not await service.refresh_content(100, "first", edited_at)
    assert not await service.refresh_content(999, "unknown", edited_at)
    assert await service.refresh_content(100, "edited offline", edited_at)
    assert await service.refresh_content(100, "edited again", None)

    stored = await service.get(100)
    assert stored is not None
    assert (stored.content, stored.edited_at) == ("edited again", edited_at)


async def test_message_repository_rolls_back_all_changes_and_rejects_reuse(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = MessageService(uow_factory=factory)
    await service.observe(MessageSnapshot(id=100, channel_id=20, author_id=30, content="original"))

    async with factory() as uow:
        messages = uow.messages
        loaded = await messages.get_by_id(100)
        assert loaded is not None
        await messages.upsert_snapshot(MessageSnapshot(id=100, channel_id=20, author_id=30, content="refreshed"))
        assert loaded.content == "refreshed"
        await messages.record_edit(100, "abandoned", Instant.now())
        await messages.mark_deleted(100, Instant.now())
        await messages.upsert_snapshot(MessageSnapshot(id=101, channel_id=20, author_id=30))
        changed = await messages.get_by_id(100)
        assert changed is not None
        assert changed.is_deleted

    stored = await service.get(100)
    assert stored is not None
    assert stored.content == "original"
    assert stored.deleted_at is None
    assert await service.get(101) is None
    with pytest.raises(InvalidStateError):
        await messages.get_by_id(100)
