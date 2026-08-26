"""Typed, scoped values with fixed or touchable expiry."""

import asyncio
import json
import math
import sqlite3
import time
from collections.abc import Callable, Hashable
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from squid_stores.stores import _validate_table_name

if TYPE_CHECKING:
    from asyncpg import Pool
else:
    try:
        from asyncpg import Pool
    except ModuleNotFoundError:
        Pool = object


_DEFAULT_TABLE_NAME = "squid_scoped_values"
_SCHEMA_VERSION = 1
_MISSING = object()


class SlotVersionError(ValueError):
    """A stored slot value was written by a newer declaration than the reader supports."""


class SlotCodec[ValueT](Protocol):
    """Encode one slot value and decode it from the version that wrote it."""

    def encode(self, value: ValueT) -> str: ...

    def decode(self, payload: str, version: int) -> ValueT: ...


@dataclass(frozen=True, slots=True)
class Slot[ScopeT: Hashable, ValueT]:
    """One declared, typed place for a value belonging to an application scope."""

    name: str
    codec: SlotCodec[ValueT]
    ttl: timedelta | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            msg = "slot name must not be empty"
            raise ValueError(msg)
        if self.version < 1:
            msg = "slot version must be positive"
            raise ValueError(msg)
        _ttl_seconds(self.ttl)


@runtime_checkable
class ScopedStore(Protocol):
    """Store declared values by exact slot and application scope."""

    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None: ...

    async def put[S: Hashable, V](
        self,
        slot: Slot[S, V],
        scope: S,
        value: V,
        *,
        ttl: timedelta | None = None,
    ) -> None: ...

    async def delete[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool: ...

    async def purge(self) -> int: ...


@dataclass(slots=True)
class _MemoryValue:
    payload: str
    version: int
    ttl_seconds: float | None
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class _StoredValue:
    payload: str
    version: int
    ttl_seconds: float | None
    expires_at: float | None


def _ttl_seconds(ttl: timedelta | None) -> float | None:
    if ttl is None:
        return None
    seconds = ttl.total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        msg = "slot TTL must be a finite positive duration"
        raise ValueError(msg)
    return seconds


def _effective_ttl(slot: Slot[Any, Any], ttl: timedelta | None) -> float | None:
    return _ttl_seconds(slot.ttl if ttl is None else ttl)


def _encode_scope(scope: Hashable, encoder: Callable[[Hashable], str] | None) -> str:
    encoded = _default_scope_key(scope) if encoder is None else encoder(scope)
    if not isinstance(encoded, str):
        msg = "scope encoder must return a string"
        raise TypeError(msg)
    if not encoded:
        msg = "encoded scope must not be empty"
        raise ValueError(msg)
    return encoded


def _default_scope_key(scope: Hashable) -> str:
    """Encode common scopes without claiming to define an application's wire format."""
    if isinstance(scope, str):
        return scope
    if isinstance(scope, bytes):
        return f"bytes:{scope.hex()}"
    if isinstance(scope, bool):
        return f"bool:{scope}"
    if isinstance(scope, int | float):
        return f"{type(scope).__name__}:{scope}"
    return repr(scope)


def _decode(slot: Slot[Any, Any], stored: _StoredValue) -> Any:
    if stored.version > slot.version:
        msg = f"slot {slot.name!r} contains version {stored.version}, newer than declared version {slot.version}"
        raise SlotVersionError(msg)
    return slot.codec.decode(stored.payload, stored.version)


class MemoryScopedStore:
    """In-process implementation of ScopedStore."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._values: dict[tuple[str, Hashable], _MemoryValue] = {}
        self._clock = clock
        self._lock = asyncio.Lock()

    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None:
        key = (slot.name, scope)
        async with self._lock:
            value = self._values.get(key)
            if value is None or self._expired(value.expires_at):
                return None
            if touch and value.ttl_seconds is not None:
                value.expires_at = self._clock() + value.ttl_seconds
            stored = _StoredValue(value.payload, value.version, value.ttl_seconds, value.expires_at)
        return _decode(slot, stored)

    async def put[S: Hashable, V](
        self,
        slot: Slot[S, V],
        scope: S,
        value: V,
        *,
        ttl: timedelta | None = None,
    ) -> None:
        payload = slot.codec.encode(value)
        if not isinstance(payload, str):
            msg = "slot codec encode() must return a string"
            raise TypeError(msg)
        ttl_seconds = _effective_ttl(slot, ttl)
        expires_at = None if ttl_seconds is None else self._clock() + ttl_seconds
        async with self._lock:
            self._values[(slot.name, scope)] = _MemoryValue(payload, slot.version, ttl_seconds, expires_at)

    async def delete[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool:
        async with self._lock:
            return self._values.pop((slot.name, scope), _MISSING) is not _MISSING

    async def purge(self) -> int:
        now = self._clock()
        async with self._lock:
            expired = [
                key for key, value in self._values.items() if value.expires_at is not None and value.expires_at <= now
            ]
            for key in expired:
                del self._values[key]
            return len(expired)

    def _expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and expires_at <= self._clock()


class SQLiteScopedStore:
    """Persist scoped values in SQLite using sqlite3 outside the event loop.

    SQLite expiry uses the supplied process wall clock. Every process sharing one
    database file must therefore have a sufficiently synchronized clock.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        table_name: str = _DEFAULT_TABLE_NAME,
        clock: Callable[[], float] = time.time,
        scope_encoder: Callable[[Hashable], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.table_name = _validate_table_name(table_name)
        self._metadata_table = f"{self.table_name}_metadata"
        self._clock = clock
        self._scope_encoder = scope_encoder
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None:
        scope_key = _encode_scope(scope, self._scope_encoder)
        await self._initialize()
        stored = await asyncio.to_thread(self._get, slot.name, scope_key, touch)
        return None if stored is None else _decode(slot, stored)

    async def put[S: Hashable, V](
        self,
        slot: Slot[S, V],
        scope: S,
        value: V,
        *,
        ttl: timedelta | None = None,
    ) -> None:
        scope_key = _encode_scope(scope, self._scope_encoder)
        payload = slot.codec.encode(value)
        if not isinstance(payload, str):
            msg = "slot codec encode() must return a string"
            raise TypeError(msg)
        ttl_seconds = _effective_ttl(slot, ttl)
        await self._initialize()
        await asyncio.to_thread(self._put, slot.name, scope_key, payload, slot.version, ttl_seconds)

    async def delete[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool:
        scope_key = _encode_scope(scope, self._scope_encoder)
        await self._initialize()
        return await asyncio.to_thread(self._drop, slot.name, scope_key)

    async def purge(self) -> int:
        await self._initialize()
        return await asyncio.to_thread(self._purge_expired)

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
                    slot_name TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    ttl_seconds REAL,
                    expires_at REAL,
                    PRIMARY KEY (slot_name, scope_key),
                    CHECK (version > 0),
                    CHECK (ttl_seconds IS NULL OR ttl_seconds > 0),
                    CHECK ((ttl_seconds IS NULL AND expires_at IS NULL) OR
                          (ttl_seconds IS NOT NULL AND expires_at IS NOT NULL))
                )
                """
            )
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {self._metadata_table} (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            row = connection.execute(
                f"SELECT value FROM {self._metadata_table} WHERE name = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {self._metadata_table} (name, value) VALUES ('schema_version', ?)",
                    (_SCHEMA_VERSION,),
                )
            elif int(row[0]) != _SCHEMA_VERSION:
                msg = f"scoped store schema {row[0]} does not match supported schema {_SCHEMA_VERSION}"
                raise RuntimeError(msg)

    def _get(self, slot_name: str, scope_key: str, touch: bool) -> _StoredValue | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT payload, version, ttl_seconds, expires_at
                FROM {self.table_name}
                WHERE slot_name = ? AND scope_key = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (slot_name, scope_key, self._clock()),
            ).fetchone()
            if row is None:
                return None
            expires_at = None if row[3] is None else float(row[3])
            if touch and row[2] is not None:
                expires_at = self._clock() + float(row[2])
                connection.execute(
                    f"UPDATE {self.table_name} SET expires_at = ? WHERE slot_name = ? AND scope_key = ?",
                    (expires_at, slot_name, scope_key),
                )
                connection.commit()
        return _StoredValue(str(row[0]), int(row[1]), None if row[2] is None else float(row[2]), expires_at)

    def _put(self, slot_name: str, scope_key: str, payload: str, version: int, ttl_seconds: float | None) -> None:
        expires_at = None if ttl_seconds is None else self._clock() + ttl_seconds
        with closing(self._connect()) as connection, connection:
            connection.execute(
                f"""
                INSERT INTO {self.table_name}
                    (slot_name, scope_key, payload, version, ttl_seconds, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot_name, scope_key) DO UPDATE SET
                    payload = excluded.payload,
                    version = excluded.version,
                    ttl_seconds = excluded.ttl_seconds,
                    expires_at = excluded.expires_at
                """,
                (slot_name, scope_key, payload, version, ttl_seconds, expires_at),
            )

    def _drop(self, slot_name: str, scope_key: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"DELETE FROM {self.table_name} WHERE slot_name = ? AND scope_key = ?",
                (slot_name, scope_key),
            )
        return cursor.rowcount == 1

    def _purge_expired(self) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"DELETE FROM {self.table_name} WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (self._clock(),),
            )
        return cursor.rowcount


class PostgresScopedStore:
    """Persist scoped values in PostgreSQL with database-owned expiry deadlines."""

    def __init__(
        self,
        pool: Pool,
        *,
        table_name: str = _DEFAULT_TABLE_NAME,
        scope_encoder: Callable[[Hashable], str] | None = None,
    ) -> None:
        self.pool = pool
        self.table_name = _validate_table_name(table_name)
        self._metadata_table = f"{self.table_name}_metadata"
        self._scope_encoder = scope_encoder
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None:
        scope_key = _encode_scope(scope, self._scope_encoder)
        await self._initialize()
        async with self.pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                f"""
                    SELECT payload, version, ttl_seconds, expires_at
                    FROM {self.table_name}
                    WHERE slot_name = $1 AND scope_key = $2
                      AND (expires_at IS NULL OR expires_at > clock_timestamp())
                    """,
                slot.name,
                scope_key,
            )
            if row is None:
                return None
            ttl_seconds = row["ttl_seconds"]
            if touch and ttl_seconds is not None:
                await connection.execute(
                    f"""
                        UPDATE {self.table_name}
                        SET expires_at = clock_timestamp() + ($3 * INTERVAL '1 second')
                        WHERE slot_name = $1 AND scope_key = $2
                        """,
                    slot.name,
                    scope_key,
                    float(ttl_seconds),
                )
            stored = _StoredValue(
                str(row["payload"]),
                int(row["version"]),
                None if ttl_seconds is None else float(ttl_seconds),
                None,
            )
        return _decode(slot, stored)

    async def put[S: Hashable, V](
        self,
        slot: Slot[S, V],
        scope: S,
        value: V,
        *,
        ttl: timedelta | None = None,
    ) -> None:
        scope_key = _encode_scope(scope, self._scope_encoder)
        payload = slot.codec.encode(value)
        if not isinstance(payload, str):
            msg = "slot codec encode() must return a string"
            raise TypeError(msg)
        ttl_seconds = _effective_ttl(slot, ttl)
        await self._initialize()
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                f"""
                INSERT INTO {self.table_name}
                    (slot_name, scope_key, payload, version, ttl_seconds, expires_at)
                VALUES (
                    $1, $2, $3, $4, $5,
                    CASE WHEN $5 IS NULL THEN NULL
                         ELSE clock_timestamp() + ($5 * INTERVAL '1 second') END
                )
                ON CONFLICT(slot_name, scope_key) DO UPDATE SET
                    payload = excluded.payload,
                    version = excluded.version,
                    ttl_seconds = excluded.ttl_seconds,
                    expires_at = excluded.expires_at
                """,
                slot.name,
                scope_key,
                payload,
                slot.version,
                ttl_seconds,
            )

    async def delete[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool:
        scope_key = _encode_scope(scope, self._scope_encoder)
        await self._initialize()
        async with self.pool.acquire() as connection, connection.transaction():
            result = await connection.execute(
                f"DELETE FROM {self.table_name} WHERE slot_name = $1 AND scope_key = $2",
                slot.name,
                scope_key,
            )
        return result.endswith("1")

    async def purge(self) -> int:
        await self._initialize()
        async with self.pool.acquire() as connection, connection.transaction():
            result = await connection.execute(
                f"DELETE FROM {self.table_name} WHERE expires_at IS NOT NULL AND expires_at <= clock_timestamp()"
            )
        try:
            return int(result.rsplit(" ", 1)[-1])
        except TypeError, ValueError:
            return 0

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            async with self.pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        slot_name TEXT NOT NULL,
                        scope_key TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK (version > 0),
                        ttl_seconds DOUBLE PRECISION,
                        expires_at TIMESTAMPTZ,
                        PRIMARY KEY (slot_name, scope_key),
                        CHECK ((ttl_seconds IS NULL AND expires_at IS NULL) OR
                              (ttl_seconds IS NOT NULL AND expires_at IS NOT NULL))
                    )
                    """
                )
                await connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._metadata_table} (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
                )
                row = await connection.fetchrow(
                    f"SELECT value FROM {self._metadata_table} WHERE name = 'schema_version'"
                )
                if row is None:
                    await connection.execute(
                        f"INSERT INTO {self._metadata_table} (name, value) VALUES ('schema_version', $1)",
                        _SCHEMA_VERSION,
                    )
                elif int(row["value"]) != _SCHEMA_VERSION:
                    msg = f"scoped store schema {row['value']} does not match supported schema {_SCHEMA_VERSION}"
                    raise RuntimeError(msg)
            self._initialized = True


@dataclass(frozen=True, slots=True)
class JsonSlotCodec[ValueT]:
    """A small JSON codec for values already represented by JSON-compatible data."""

    decoder: Callable[[Any, int], ValueT] | None = None

    def encode(self, value: ValueT) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def decode(self, payload: str, version: int) -> ValueT:
        value = json.loads(payload)
        if self.decoder is None:
            return value
        return self.decoder(value, version)


def json_codec[ValueT](decoder: Callable[[Any, int], ValueT] | None = None) -> JsonSlotCodec[ValueT]:
    """Build a JSON slot codec, optionally applying a version-aware decoder."""
    return JsonSlotCodec(decoder)


__all__ = [
    "JsonSlotCodec",
    "MemoryScopedStore",
    "PostgresScopedStore",
    "SQLiteScopedStore",
    "ScopedStore",
    "Slot",
    "SlotCodec",
    "SlotVersionError",
    "json_codec",
]
