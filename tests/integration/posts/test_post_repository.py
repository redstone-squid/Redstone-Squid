"""Post persistence, replacement, and render tracking."""

from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import InvalidStateError
from squid.messages.application.services import MessageService
from squid.messages.domain import MessageFact
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.posts.application.services import PostService
from tests.support.database import assert_loaded_detached

CHANNEL = 5000
OTHER_CHANNEL = 5001


async def _seed_guild(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory.begin() as session:
        await session.execute(text("INSERT INTO server_settings (server_id) VALUES (4000)"))


async def _observe(session_factory: async_sessionmaker[AsyncSession], *message_ids: int) -> None:
    """Record the message facts the posts will point at."""
    messages = MessageService(uow_factory=partial(SqlAlchemyUnitOfWork, session_factory))
    for message_id in message_ids:
        await messages.observe(
            MessageFact(id=message_id, channel_id=CHANNEL, author_id=7000, guild_id=4000, content=None)
        )


async def test_one_live_post_per_resource_and_channel(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))

    await posts.record(
        message_id=100,
        channel_id=CHANNEL,
        resource_kind="build",
        resource_key="42",
        surface="build_card",
        applied_revision=1,
    )

    with pytest.raises(IntegrityError):
        await posts.record(
            message_id=101,
            channel_id=CHANNEL,
            resource_kind="build",
            resource_key="42",
            surface="build_card",
            applied_revision=1,
        )


async def test_recording_the_same_message_twice_is_a_noop(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))

    for _ in range(2):
        await posts.record(
            message_id=100,
            channel_id=CHANNEL,
            resource_kind="build",
            resource_key="42",
            surface="build_card",
            applied_revision=1,
        )

    assert len(await posts.list_for_resource("build", "42")) == 1


async def test_suppressing_frees_the_channel_for_a_replacement(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    await posts.record(
        message_id=100,
        channel_id=CHANNEL,
        resource_kind="starboard_entry",
        resource_key="1:2",
        surface="starboard_entry",
        applied_revision=1,
    )

    assert await posts.suppress(100) is True
    # A redelivered raw delete event must not move the tombstone.
    assert await posts.suppress(100) is False

    await posts.record(
        message_id=101,
        channel_id=CHANNEL,
        resource_kind="starboard_entry",
        resource_key="1:2",
        surface="starboard_entry",
        applied_revision=1,
    )
    live = [post for post in await posts.list_for_resource("starboard_entry", "1:2") if post.is_live]
    assert [post.message_id for post in live] == [101]


async def test_applied_revision_never_moves_backwards(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    await posts.record(
        message_id=100,
        channel_id=CHANNEL,
        resource_kind="build",
        resource_key="42",
        surface="build_card",
        applied_revision=1,
    )

    await posts.mark_applied("build", "42", 9)
    await posts.mark_applied("build", "42", 4)

    assert (await posts.list_for_resource("build", "42"))[0].applied_revision == 9


async def test_service_returns_detached_posts(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    await posts.record(
        message_id=100,
        channel_id=CHANNEL,
        resource_kind="build",
        resource_key="42",
        surface="build_card",
        applied_revision=1,
    )

    post = (await posts.list_for_resource("build", "42"))[0]
    assert_loaded_detached(post)
    post.applied_revision = 99
    assert (await posts.list_for_resource("build", "42"))[0].applied_revision == 1


async def test_pending_generation_reads_desired_state_from_the_queue(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    for message_id, channel_id in ((100, CHANNEL), (101, OTHER_CHANNEL)):
        await posts.record(
            message_id=message_id,
            channel_id=channel_id,
            resource_kind="build",
            resource_key="42",
            surface="build_card",
            applied_revision=1,
        )

    async with migrated_session_factory.begin() as session:
        generation = (
            await session.execute(
                text(
                    "INSERT INTO discord_sync_queue (resource_kind, source_key, action) "
                    "VALUES ('build', '42', 'refresh') RETURNING generation"
                )
            )
        ).scalar_one()

    assert await posts.pending_generation("build", "42") == generation

    # One post rendered, the other not: the resource is still behind.
    await posts.mark_rendered(100, generation)
    assert await posts.pending_generation("build", "42") == generation

    await posts.mark_rendered(101, generation)
    assert await posts.pending_generation("build", "42") is None


async def test_a_post_cannot_outlive_its_message_fact(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    await posts.record(
        message_id=100,
        channel_id=CHANNEL,
        resource_kind="build",
        resource_key="42",
        surface="build_card",
        applied_revision=1,
    )

    with pytest.raises(IntegrityError):
        async with migrated_session_factory.begin() as session:
            await session.execute(text("DELETE FROM messages WHERE id = 100"))


@pytest.mark.parametrize("commit", [False, True])
async def test_settings_messages_and_posts_share_one_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    commit: bool,
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with factory() as uow:
        await uow.settings.get_or_create_for_update(4000)
        await uow.messages.upsert_fact(MessageFact(id=100, channel_id=CHANNEL, author_id=7000, guild_id=4000))
        await uow.posts.record(
            message_id=100,
            channel_id=CHANNEL,
            resource_kind="build",
            resource_key="42",
            surface="build_card",
            applied_revision=1,
        )
        if commit:
            await uow.commit()

    async with factory() as uow:
        assert (await uow.settings.get(4000) is not None) == commit
        assert (await uow.messages.get_by_id(100) is not None) == commit
        assert (await uow.posts.resolve(100) is not None) == commit


async def test_post_updates_and_deletion_roll_back_together(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    posts = PostService(uow_factory=factory)
    for message_id, channel_id in ((100, CHANNEL), (101, OTHER_CHANNEL)):
        await posts.record(
            message_id=message_id,
            channel_id=channel_id,
            resource_kind="build",
            resource_key="42",
            surface="build_card",
            applied_revision=1,
        )

    async with factory() as uow:
        repository = uow.posts
        loaded = await repository.list_for_resource("build", "42")
        await repository.mark_applied("build", "42", 9)
        await repository.mark_rendered(100, 4)
        assert loaded[0].applied_revision == 9
        assert await repository.suppress(100)
        assert not loaded[0].is_live
        await repository.forget(101)
        assert len(await repository.list_for_resource("build", "42")) == 1

    stored = await posts.list_for_resource("build", "42")
    assert len(stored) == 2
    assert all(post.applied_revision == 1 and post.is_live for post in stored)
    with pytest.raises(InvalidStateError):
        await repository.forget(100)

    await posts.forget(100)
    assert await posts.resolve(100) is None
    assert await posts.resolve(101) is not None
