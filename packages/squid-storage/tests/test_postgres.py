"""Database-specific storage concurrency regressions."""

from datetime import timedelta

import anyio
from asyncpg import Pool

from squid_storage import AdmissionToken, ClaimToken, PostgresScopedStore, PostgresSessionStore, Slot, json_codec


async def test_postgres_admissions_serialize_unused_destination_keys(postgres_pool: Pool) -> None:
    pool = postgres_pool
    table = "squid_sessions"
    application_name = await pool.fetchval("SELECT current_setting('application_name')")
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
                await pool.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
                    application_name,
                )
                < 2
            ):
                await anyio.sleep(0.01)
    assert sum(token is not None for token in results) == 1
    record = await store.load("shared")
    assert record is not None
    loser = second if record.scope == "first" else first
    assert await store.inspect(loser) == ()


async def test_postgres_scoped_instances_initialize_concurrently(postgres_pool: Pool) -> None:
    pool = postgres_pool
    table = "scoped_initialization"
    stores = [PostgresScopedStore(pool, table_name=table) for _ in range(8)]
    slot = Slot[str, int]("value", json_codec())
    async with anyio.create_task_group() as tasks:
        for index, store in enumerate(stores):
            tasks.start_soon(store.put, slot, str(index), index)
    for index in range(len(stores)):
        assert await stores[0].get(slot, str(index)) == index


async def test_postgres_touch_reads_the_replacement_ttl(postgres_pool: Pool) -> None:
    pool = postgres_pool
    store = PostgresScopedStore(pool)
    slot = Slot[str, int]("value", json_codec(), ttl=timedelta(seconds=10))
    await store.put(slot, "scope", 1)
    application_name = await pool.fetchval("SELECT current_setting('application_name')")
    results: list[int | None] = []

    async def touch() -> None:
        results.append(await store.get(slot, "scope", touch=True))

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks, pool.acquire() as blocker, blocker.transaction():
            await blocker.execute("SELECT 1 FROM squid_scoped_values FOR UPDATE")
            tasks.start_soon(touch)
            while not await pool.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1 AND wait_event_type = 'Lock'",
                application_name,
            ):
                await anyio.sleep(0.01)
            # Commit a replacement while the touching reader waits for this row.
            await blocker.execute(
                "UPDATE squid_scoped_values SET payload = '2', ttl_seconds = 100, "
                "expires_at = clock_timestamp() + INTERVAL '100 seconds'"
            )
    assert results == [2]
    remaining = await pool.fetchval("SELECT expires_at - clock_timestamp() FROM squid_scoped_values")
    assert remaining > timedelta(seconds=90)
