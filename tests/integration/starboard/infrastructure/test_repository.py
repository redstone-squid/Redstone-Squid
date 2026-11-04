"""Vote replacement and source matching using the starboard ORM tables without migration triggers."""

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.models import ServerSetting
from squid.starboard.application import PendingVote
from squid.starboard.models import (
    OriginMessage,
    StarboardConfig,
    StarboardEmoji,
    StarboardEntry,
    StarboardRoleMultiplier,
    StarboardSource,
    StarboardVote,
)
from squid.starboard.models import (
    StarboardEmoji as StarboardEmojiRow,
)

TABLES = cast(
    tuple[Table, ...],
    (
        ServerSetting.__table__,
        StarboardConfig.__table__,
        StarboardEmojiRow.__table__,
        StarboardSource.__table__,
        OriginMessage.__table__,
        StarboardVote.__table__,
        StarboardEntry.__table__,
        StarboardRoleMultiplier.__table__,
    ),
)


@pytest.fixture(autouse=True)
async def starboard_schema(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        for table in TABLES:
            await connection.run_sync(table.create)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            for table in reversed(TABLES):
                await connection.run_sync(table.drop)


async def seed_guild(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        await session.execute(insert(ServerSetting).values(server_id=10))


async def test_vote_overwrite_and_exact_emoji_withdrawal(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_guild(async_session_factory)
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        created = await uow.starboards.create(
            StarboardConfig(
                id=0,
                guild_id=10,
                channel_id=20,
                name="main",
                emojis=(StarboardEmoji(emoji="⭐", direction="up"), StarboardEmoji(emoji="💩", direction="down")),
                required=1,
            )
        )
        await uow.commit()
    origin = OriginMessage(
        id=100, guild_id=10, channel_id=30, author_id=40, author_is_bot=False, posted_at=Instant.now()
    )

    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        keys = await uow.starboards.record_votes(origin, 50, (PendingVote(created, "⭐", "up", 1),))
        await uow.commit()
    assert keys == [(created.id, origin.id)]
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        state = await uow.starboards.entry_state(created.id, origin.id)
    assert state is not None
    assert state.entry.score == 1

    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        await uow.starboards.record_votes(origin, 50, (PendingVote(created, "💩", "down", 1),))
        await uow.commit()
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        state = await uow.starboards.entry_state(created.id, origin.id)
    assert state is not None
    assert state.entry.score == -1

    # Withdrawing an emoji the member did not cast changes nothing.
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        assert await uow.starboards.withdraw_vote(origin.id, 50, "⭐") == ()
        await uow.commit()

    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        await uow.starboards.withdraw_vote(origin.id, 50, "💩")
        await uow.commit()
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        state = await uow.starboards.entry_state(created.id, origin.id)
    assert state is not None
    assert state.entry.score == 0
    assert state.entry.raw_count == 0


async def test_whole_guild_and_channel_sources_do_not_duplicate_configs(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_guild(async_session_factory)
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        created = await uow.starboards.create(
            StarboardConfig(
                id=0, guild_id=10, channel_id=20, name="main", emojis=(StarboardEmoji(emoji="⭐", direction="up"),)
            )
        )
        await uow.commit()
    async with async_session_factory.begin() as session:
        await session.execute(insert(StarboardSource).values(starboard_id=created.id, guild_id=10, channel_id=30))

    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        configs = await uow.starboards.configs_for_source(10, 30)

    assert [item.id for item in configs] == [created.id]
