"""Postgres snapshot store integration coverage."""

import uuid

import asyncpg
from testcontainers.postgres import PostgresContainer

from squid_layouts.discord.durability import PostgresSessionStore


async def test_postgres_snapshot_store_contract(postgres_container: PostgresContainer) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    table_name = f"snapshot_test_{uuid.uuid4().hex}"
    store = PostgresSessionStore(pool, table_name=table_name)
    try:
        second_admission = await store.reserve("scope:second", "writer", 10.0)
        assert second_admission is not None
        second = await store.commit(
            second_admission,
            key="second",
            summary_payload="summary 2",
            snapshot_payload="payload 2",
            victims=(),
            lease_seconds=10.0,
        )
        first_admission = await store.reserve("scope:first", "writer", 10.0)
        assert first_admission is not None
        first = await store.commit(
            first_admission,
            key="first",
            summary_payload="summary 1",
            snapshot_payload="payload 1",
            victims=(),
            lease_seconds=10.0,
        )
        assert first is not None
        assert second is not None
        assert tuple(record.key for record in await store.list_records()) == ("first", "second")
        loaded = await store.load("first")
        assert loaded is not None
        assert loaded.snapshot_payload == "payload 1"

        assert await store.release(first)
        owner = await store.claim("first", "owner", 10.0)
        assert owner is not None
        assert await store.claim("first", "contender", 10.0) is None
        assert await store.renew(owner, 10.0)
        assert await store.release(owner)
        contender = await store.claim("first", "contender", 10.0)
        assert contender is not None
        assert not await store.save(owner, "stale", "stale")
        assert await store.delete(contender)
        assert await store.load("first") is None
    finally:
        await pool.execute(f"DROP TABLE IF EXISTS {table_name}, {table_name}_metadata, {table_name}_admissions")
        await pool.execute(f"DROP SEQUENCE IF EXISTS {table_name}_fence_seq")
        await pool.close()
