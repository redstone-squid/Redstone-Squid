"""Ready-to-use durable snapshot stores."""

import asyncio
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from os import PathLike
from pathlib import Path

_SCHEMA_VERSION = 1
_DEFAULT_TABLE_NAME = "squid_layout_snapshots"


class SQLiteSnapshotStore:
    """Persist leased snapshots in a SQLite file.

    Args:
        path: Database file path. Parent directories must already exist.
        table_name: Unqualified database table name.
        clock: Wall clock used to decide whether an existing lease has expired.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        table_name: str = _DEFAULT_TABLE_NAME,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.table_name = _validate_table_name(table_name)
        self._clock = clock
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def load(self, key: str) -> str | None:
        await self._initialize()
        return await asyncio.to_thread(self._load, key)

    async def save(self, key: str, payload: str) -> None:
        await self._initialize()
        await asyncio.to_thread(self._save, key, payload)

    async def delete(self, key: str) -> None:
        await self._initialize()
        await asyncio.to_thread(self._delete, key)

    async def list_keys(self) -> tuple[str, ...]:
        await self._initialize()
        return await asyncio.to_thread(self._list_keys)

    async def claim(self, key: str, owner: str, lease_until: float) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._claim, key, owner, lease_until)

    async def renew(self, key: str, owner: str, lease_until: float) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._renew, key, owner, lease_until)

    async def release(self, key: str, owner: str) -> None:
        await self._initialize()
        await asyncio.to_thread(self._release, key, owner)

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if not self._initialized:
                await asyncio.to_thread(self._open)
                self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _open(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    owner TEXT,
                    lease_until REAL,
                    schema_version INTEGER NOT NULL DEFAULT {_SCHEMA_VERSION}
                )
                """
            )
            version = connection.execute(f"SELECT MAX(schema_version) FROM {self.table_name}").fetchone()[0]
            if version is not None and version > _SCHEMA_VERSION:
                message = f"snapshot store schema {version} is newer than supported version {_SCHEMA_VERSION}"
                raise RuntimeError(message)

    def _load(self, key: str) -> str | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(f"SELECT payload FROM {self.table_name} WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _save(self, key: str, payload: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                f"""
                INSERT INTO {self.table_name} (key, payload, schema_version) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload,
                    schema_version = excluded.schema_version
                """,
                (key, payload, _SCHEMA_VERSION),
            )

    def _delete(self, key: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(f"DELETE FROM {self.table_name} WHERE key = ?", (key,))

    def _list_keys(self) -> tuple[str, ...]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(f"SELECT key FROM {self.table_name} ORDER BY key").fetchall()
        return tuple(str(row[0]) for row in rows)

    def _claim(self, key: str, owner: str, lease_until: float) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table_name} SET owner = ?, lease_until = ?
                WHERE key = ? AND (owner = ? OR lease_until IS NULL OR lease_until < ?)
                """,
                (owner, lease_until, key, owner, self._clock()),
            )
        return cursor.rowcount == 1

    def _renew(self, key: str, owner: str, lease_until: float) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"UPDATE {self.table_name} SET lease_until = ? WHERE key = ? AND owner = ?",
                (lease_until, key, owner),
            )
        return cursor.rowcount == 1

    def _release(self, key: str, owner: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                f"UPDATE {self.table_name} SET owner = NULL, lease_until = NULL WHERE key = ? AND owner = ?",
                (key, owner),
            )


def _validate_table_name(table_name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
        message = "snapshot table name must be an unqualified SQL identifier"
        raise ValueError(message)
    return table_name
