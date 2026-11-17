"""Postgres durable-session store integration coverage."""

import uuid

import anyio
import asyncpg
from testcontainers.postgres import PostgresContainer

from squid_storage import AdmissionToken, ClaimToken, PostgresSessionStore


async def test_postgres_session_store_contract(postgres_container: PostgresContainer) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    table_name = f"session_test_{uuid.uuid4().hex}"
    store = PostgresSessionStore(pool, table_name=table_name)
    try:
        second_admission = await store.reserve("scope:second", "writer", 10.0)
        assert second_admission is not None
        second = await store.commit(
            second_admission,
            key="second",
            snapshot_payload="snapshot 2",
            record_payload="payload 2",
            victims=(),
            lease_seconds=10.0,
        )
        first_admission = await store.reserve("scope:first", "writer", 10.0)
        assert first_admission is not None
        first = await store.commit(
            first_admission,
            key="first",
            snapshot_payload="snapshot 1",
            record_payload="payload 1",
            victims=(),
            lease_seconds=10.0,
        )
        assert first is not None
        assert second is not None
        assert tuple(record.key for record in await store.list()) == ("first", "second")
        loaded = await store.load("first")
        assert loaded is not None
        assert loaded.record_payload == "payload 1"

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


async def test_postgres_admissions_serialize_unused_destination_keys(postgres_container: PostgresContainer) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    table = f"session_test_{uuid.uuid4().hex}"
    pool = await asyncpg.create_pool(dsn, server_settings={"application_name": table})
    assert pool is not None
    store = PostgresSessionStore(pool, table_name=table)
    results: list[ClaimToken | None] = []

    async def commit(reservation: AdmissionToken) -> None:
        results.append(
            await store.commit(
                reservation,
                key="shared",
                snapshot_payload="snapshot",
                record_payload="record",
                victims=(),
                lease_seconds=30,
            )
        )

    try:
        first = await store.reserve("first", "owner", 30)
        second = await store.reserve("second", "owner", 30)
        assert first is not None
        assert second is not None
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks, pool.acquire() as blocker, blocker.transaction():
                # Both calls can read the absent key, but inserts must wait. Once
                # released, conflicting admissions must settle as one winner and None.
                await blocker.execute(f"LOCK TABLE {table} IN SHARE MODE")
                tasks.start_soon(commit, first)
                tasks.start_soon(commit, second)
                while (
                    await blocker.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
                        table,
                    )
                    < 2
                ):
                    await anyio.sleep(0.01)
        assert sum(token is not None for token in results) == 1
        record = await store.load("shared")
        assert record is not None
        loser = second if record.scope == "first" else first
        assert await store.inspect(loser) == ()
    finally:
        await pool.execute(f"DROP TABLE IF EXISTS {table}, {table}_metadata, {table}_admissions")
        await pool.execute(f"DROP SEQUENCE IF EXISTS {table}_fence_seq")
        await pool.close()
