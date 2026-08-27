"""PostgreSQL coverage for the public scoped-store contract."""

from datetime import timedelta

import anyio
import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

from squid_storage import PostgresScopedStore, Slot, SlotVersionError, json_codec


async def test_postgres_scoped_store_round_trip_version_expiry_touch_and_purge(
    postgres_container: PostgresContainer,
) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    store = PostgresScopedStore(pool, table_name="squid_scoped_contract")
    slot = Slot[str, dict[str, int] | None]("preferences", json_codec(), version=2, ttl=timedelta(seconds=0.3))

    try:
        await store.put(slot, "guild", {"theme": 1})
        assert await store.get(slot, "guild") == {"theme": 1}

        older = Slot[str, dict[str, int] | None]("preferences", json_codec(), version=1)
        with pytest.raises(SlotVersionError, match="newer than declared"):
            await store.get(older, "guild")

        await anyio.sleep(0.2)
        assert await store.get(slot, "guild", touch=True) == {"theme": 1}
        await anyio.sleep(0.2)
        assert await store.get(slot, "guild") == {"theme": 1}
        await anyio.sleep(0.2)
        assert await store.get(slot, "guild") is None
        assert await store.purge() == 1

        await store.put(slot, "delete", None, ttl=None)
        assert await store.delete(slot, "delete")
        assert not await store.delete(slot, "delete")
    finally:
        await pool.close()
