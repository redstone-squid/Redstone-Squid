"""Postgres snapshot store integration coverage."""

import uuid

import asyncpg
from testcontainers.postgres import PostgresContainer

from squid_layouts.discord.durability import PostgresSnapshotStore


async def test_postgres_snapshot_store_contract(postgres_container: PostgresContainer) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    table_name = f"snapshot_test_{uuid.uuid4().hex}"
    store = PostgresSnapshotStore(pool, table_name=table_name, clock=lambda: 100.0)
    try:
        await store.save("second", "payload 2")
        await store.save("first", "payload 1")
        assert await store.list_keys() == ("first", "second")
        assert await store.load("first") == "payload 1"
        assert await store.claim("first", "owner", 110.0)
        assert not await store.claim("first", "contender", 120.0)
        assert await store.renew("first", "owner", 120.0)
        await store.release("first", "owner")
        assert await store.claim("first", "contender", 130.0)
        await store.delete("first")
        assert await store.load("first") is None
    finally:
        await pool.execute(f"DROP TABLE IF EXISTS {table_name}")
        await pool.close()
