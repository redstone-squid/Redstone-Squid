"""Shared backend setup for the storage contracts."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from asyncpg import Pool

from squid_storage import (
    ClaimToken,
    DurableSessionStore,
    MemoryScopedStore,
    MemorySessionStore,
    PostgresScopedStore,
    PostgresSessionStore,
    ScopedStore,
    SQLiteScopedStore,
    SQLiteSessionStore,
)


@asynccontextmanager
async def isolated_postgres_pool(dsn: str) -> AsyncIterator[Pool]:
    schema = f"storage_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        try:
            async with asyncpg.create_pool(
                dsn, min_size=1, max_size=5, server_settings={"search_path": schema, "application_name": schema}
            ) as pool:
                yield pool
        finally:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")
    finally:
        await admin.close()


class StorageBackend:
    """Exercise one adapter with controlled expiry and a common session publisher."""

    def __init__(self, kind: str, path: Path, pool: Pool | None) -> None:
        self.now = 100.0
        self.pool = pool
        self.sessions: DurableSessionStore
        self.scoped: ScopedStore
        if kind == "memory":
            self.sessions = MemorySessionStore(clock=self.clock)
            self.scoped = MemoryScopedStore(clock=self.clock)
        elif kind == "sqlite":
            self.sessions = SQLiteSessionStore(path, clock=self.clock)
            self.scoped = SQLiteScopedStore(path, clock=self.clock)
        else:
            assert pool is not None
            self.sessions = PostgresSessionStore(pool)
            self.scoped = PostgresScopedStore(pool)

    def clock(self) -> float:
        return self.now

    async def advance(self, seconds: float) -> None:
        """Advance local clocks or age SQL deadlines without wall-clock sleeps."""
        self.now += seconds
        if self.pool is not None:
            # Both stores are initialized by the fixture. Keep the database clock
            # authoritative while making lease and TTL boundary tests deterministic.
            async with self.pool.acquire() as connection, connection.transaction():
                for table, column in (
                    ("squid_sessions", "lease_until"),
                    ("squid_sessions_admissions", "lease_until"),
                    ("squid_scoped_values", "expires_at"),
                ):
                    await connection.execute(
                        f"UPDATE {table} SET {column} = {column} - ($1::double precision * INTERVAL '1 second')",
                        seconds,
                    )

    async def publish(
        self,
        key: str,
        *,
        scope: str = "scope",
        owner: str = "writer",
        victims: tuple[str, ...] = (),
    ) -> ClaimToken:
        reservation = await self.sessions.reserve(scope, owner, 10.0)
        assert reservation is not None
        token = await self.sessions.commit(
            reservation,
            key=key,
            snapshot_payload=f"snapshot:{key}",
            record_payload=f"record:{key}",
            victims=victims,
            lease_seconds=10.0,
        )
        assert token is not None
        return token
