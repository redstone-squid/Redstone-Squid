"""Coverage for the guarantees `discord_posts` moves into the database."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.messages.domain import MessageFact
from squid.messages.infrastructure.repository import MessageRepository
from squid.posts.infrastructure.repository import PostRepository

CHANNEL = 5000
OTHER_CHANNEL = 5001


async def _seed_guild(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory.begin() as session:
        await session.execute(text("INSERT INTO server_settings (server_id) VALUES (4000)"))


async def _observe(session_factory: async_sessionmaker[AsyncSession], *message_ids: int) -> None:
    """Record the message facts the posts will point at."""
    messages = MessageRepository(session_factory)
    for message_id in message_ids:
        await messages.upsert_fact(
            MessageFact(id=message_id, channel_id=CHANNEL, author_id=7000, guild_id=4000, content=None)
        )


async def test_one_live_post_per_resource_and_channel(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The duplicate-card guard is a constraint, not a check each caller reimplements."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostRepository(migrated_session_factory)

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
    """A retry that already sent and already recorded must not fail."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostRepository(migrated_session_factory)

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
    """A post deleted by hand is tombstoned, and the slot reopens for a repost."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostRepository(migrated_session_factory)
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
    """Overlapping reconciler passes must not report an older render as current."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostRepository(migrated_session_factory)
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


async def test_pending_generation_reads_desired_state_from_the_queue(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Staleness is a join against the queue, not a revision copied onto each post."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100, 101)
    posts = PostRepository(migrated_session_factory)
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
    """RESTRICT keeps the fact a post points at from being deleted underneath it."""
    await _seed_guild(migrated_session_factory)
    await _observe(migrated_session_factory, 100)
    posts = PostRepository(migrated_session_factory)
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
