from collections.abc import AsyncGenerator
from typing import cast

import pytest
from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.settings.infrastructure.models import ServerSetting
from squid.starboard.application import PendingVote
from squid.starboard.domain import EntryAction, OriginMessage, StarboardConfig, StarboardEmoji
from squid.starboard.infrastructure.models import (
    Starboard,
    StarboardEntry,
    StarboardOriginMessage,
    StarboardRoleMultiplier,
    StarboardSource,
    StarboardVote,
)
from squid.starboard.infrastructure.models import (
    StarboardEmoji as StarboardEmojiRow,
)
from squid.starboard.infrastructure.repository import PostgresStarboardRepository

TABLES = cast(
    tuple[Table, ...],
    (
        ServerSetting.__table__,
        Starboard.__table__,
        StarboardEmojiRow.__table__,
        StarboardSource.__table__,
        StarboardOriginMessage.__table__,
        StarboardVote.__table__,
        StarboardEntry.__table__,
        StarboardRoleMultiplier.__table__,
    ),
)


@pytest.fixture(autouse=True)
async def starboard_schema(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with async_engine.begin() as connection:
        for table in TABLES:
            await connection.run_sync(table.create)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            for table in reversed(TABLES):
                await connection.run_sync(table.drop)


async def repository(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> PostgresStarboardRepository:
    async with async_session_factory.begin() as session:
        await session.execute(insert(ServerSetting).values(server_id=10))
    return PostgresStarboardRepository(async_session_factory)


async def test_vote_overwrite_and_exact_emoji_withdrawal(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = await repository(async_session_factory)
    created = await repo.create(
        StarboardConfig(
            0,
            10,
            20,
            "main",
            (StarboardEmoji("⭐", "up"), StarboardEmoji("💩", "down")),
            required=1,
        )
    )
    origin = OriginMessage(100, 10, 30, 40, author_is_bot=False, posted_at=Instant.now())

    plans = await repo.record_votes(origin, 50, (PendingVote(created, "⭐", "up", 1),))
    assert plans[0].action is EntryAction.SEND
    assert plans[0].entry.score == 1

    plans = await repo.record_votes(origin, 50, (PendingVote(created, "💩", "down", 1),))
    assert plans[0].entry.score == -1
    assert await repo.withdraw_vote(origin.id, 50, "⭐") == ()

    plans = await repo.withdraw_vote(origin.id, 50, "💩")
    assert plans[0].entry.score == 0
    assert plans[0].entry.raw_count == 0


async def test_whole_guild_and_channel_sources_do_not_duplicate_configs(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = await repository(async_session_factory)
    created = await repo.create(StarboardConfig(0, 10, 20, "main", (StarboardEmoji("⭐", "up"),)))
    async with async_session_factory.begin() as session:
        await session.execute(insert(StarboardSource).values(starboard_id=created.id, guild_id=10, channel_id=30))

    configs = await repo.configs_for_source(10, 30)

    assert [item.id for item in configs] == [created.id]
