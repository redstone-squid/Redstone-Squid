"""Optional asyncpg snapshot store."""

import asyncio
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncpg import Pool
else:
    try:
        from asyncpg import Pool
    except ModuleNotFoundError:
        Pool = object


_SCHEMA_VERSION = 1
_DEFAULT_TABLE_NAME = "squid_layout_snapshots"


class PostgresSnapshotStore:
    """Persist leased snapshots through an asyncpg pool.

    Args:
        pool: An asyncpg connection pool.
        table_name: Unqualified database table name.
        clock: Wall clock used to decide whether an existing lease has expired.
    """

    def __init__(
        self,
        pool: Pool,
        *,
        table_name: str = _DEFAULT_TABLE_NAME,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.pool = pool
        self.table_name = _validate_table_name(table_name)
        self._clock = clock
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def load(self, key: str) -> str | None:
        await self._initialize()
        value = await self.pool.fetchval(f"SELECT payload FROM {self.table_name} WHERE key = $1", key)
        return None if value is None else str(value)

    async def save(self, key: str, payload: str) -> None:
        await self._initialize()
        await self.pool.execute(
            f"""
            INSERT INTO {self.table_name} (key, payload, schema_version) VALUES ($1, $2, $3)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload,
                schema_version = excluded.schema_version
            """,
            key,
            payload,
            _SCHEMA_VERSION,
        )

    async def delete(self, key: str) -> None:
        await self._initialize()
        await self.pool.execute(f"DELETE FROM {self.table_name} WHERE key = $1", key)

    async def list_keys(self) -> tuple[str, ...]:
        await self._initialize()
        rows = await self.pool.fetch(f"SELECT key FROM {self.table_name} ORDER BY key")
        return tuple(str(row["key"]) for row in rows)

    async def claim(self, key: str, owner: str, lease_until: float) -> bool:
        await self._initialize()
        claimed = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name} SET owner = $2, lease_until = $3
            WHERE key = $1 AND (owner = $2 OR lease_until IS NULL OR lease_until < $4)
            RETURNING TRUE
            """,
            key,
            owner,
            lease_until,
            self._clock(),
        )
        return claimed is True

    async def renew(self, key: str, owner: str, lease_until: float) -> bool:
        await self._initialize()
        renewed = await self.pool.fetchval(
            f"UPDATE {self.table_name} SET lease_until = $3 WHERE key = $1 AND owner = $2 RETURNING TRUE",
            key,
            owner,
            lease_until,
        )
        return renewed is True

    async def release(self, key: str, owner: str) -> None:
        await self._initialize()
        await self.pool.execute(
            f"UPDATE {self.table_name} SET owner = NULL, lease_until = NULL WHERE key = $1 AND owner = $2",
            key,
            owner,
        )

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self.pool.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    owner TEXT,
                    lease_until DOUBLE PRECISION,
                    schema_version INTEGER NOT NULL DEFAULT {_SCHEMA_VERSION}
                )
                """
            )
            version = await self.pool.fetchval(f"SELECT MAX(schema_version) FROM {self.table_name}")
            if version is not None and version > _SCHEMA_VERSION:
                message = f"snapshot store schema {version} is newer than supported version {_SCHEMA_VERSION}"
                raise RuntimeError(message)
            self._initialized = True


def _validate_table_name(table_name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
        message = "snapshot table name must be an unqualified SQL identifier"
        raise ValueError(message)
    return table_name
