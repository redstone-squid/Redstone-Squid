"""The epoch trigger: one bump per statement, and a notification after commit."""

import asyncio
from collections.abc import AsyncGenerator

import asyncpg
import pytest
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.permissions.infrastructure.repository import EPOCH_CHANNEL
from squid.persistence.alembic_entities import alembic_util_entities

_CREATE_SCHEMA = """
CREATE TABLE permission_epoch (
    id SMALLINT PRIMARY KEY,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE permission_grants (
    id BIGSERIAL PRIMARY KEY,
    pattern TEXT NOT NULL,
    effect SMALLINT NOT NULL,
    subject_account_id INTEGER
);
INSERT INTO permission_epoch (id, version) VALUES (1, 1);
"""

_DROP_SCHEMA = """
DROP TABLE IF EXISTS permission_grants, permission_epoch CASCADE;
DROP FUNCTION IF EXISTS public.bump_permission_epoch() CASCADE;
"""

_ENTITY_NAMES = {"bump_permission_epoch", "permission_grants_bump_epoch"}


@pytest.fixture(autouse=True)
async def epoch_schema(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    """Install the shipped trigger over a minimal production-shaped schema."""
    statements = [
        text(entity.to_sql_statement_create().text)
        for entity in alembic_util_entities()
        if isinstance(entity, PGFunction | PGTrigger) and entity.signature.partition("(")[0] in _ENTITY_NAMES
    ]
    assert len(statements) == 2
    async with async_engine.begin() as connection:
        for statement in _CREATE_SCHEMA.strip().split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        for statement in statements:
            await connection.execute(statement)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            for statement in _DROP_SCHEMA.strip().split(";"):
                if statement.strip():
                    await connection.execute(text(statement))


async def _version(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return (await session.execute(text("SELECT version FROM permission_epoch WHERE id = 1"))).scalar_one()


async def test_a_bulk_grant_bumps_the_epoch_once(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Statement-level, so granting to fifty people is one invalidation, not fifty."""
    before = await _version(async_session_factory)

    async with async_session_factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO permission_grants (pattern, effect, subject_account_id) "
                "SELECT 'build.**', 1, generate_series(1, 50)"
            )
        )

    assert await _version(async_session_factory) == before + 1


async def test_a_delete_bumps_the_epoch_too(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Revocation is the direction staleness must never favour."""
    async with async_session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO permission_grants (pattern, effect, subject_account_id) VALUES ('build.**', 1, 1)")
        )
    before = await _version(async_session_factory)

    async with async_session_factory.begin() as session:
        await session.execute(text("DELETE FROM permission_grants"))

    assert await _version(async_session_factory) == before + 1


async def test_a_committed_write_notifies_listeners(async_engine: AsyncEngine) -> None:
    """The latency hint half of the contract; the poll is what makes it durable."""
    url = async_engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    connection = await asyncpg.connect(url)
    received: asyncio.Queue[str] = asyncio.Queue()

    async def on_notify(*args: object) -> None:
        await received.put(str(args[-1]))

    def notified(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        received.put_nowait(payload)

    try:
        await connection.add_listener(EPOCH_CHANNEL, notified)
        async with async_engine.begin() as writer:
            await writer.execute(
                text("INSERT INTO permission_grants (pattern, effect, subject_account_id) VALUES ('build.**', 1, 7)")
            )
        payload = await asyncio.wait_for(received.get(), timeout=5)
    finally:
        await connection.close()

    assert int(payload) >= 2
