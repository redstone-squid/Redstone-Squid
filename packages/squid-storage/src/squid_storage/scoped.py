"""Store typed values under a declared slot and an application scope.

A Slot names the kind of value and supplies its codec, schema version, and
default expiry. The scope identifies whose value it is: for example, the same
preferences slot can hold separate values for many member IDs. Store operations
encode and decode at this boundary; they do not retain live application objects.
"""

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

from squid_storage.stores import _validate_table_name

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


class StorageError(Exception):
    """Base class for storage-specific errors; validation can also raise standard exceptions."""


class SlotVersionError(StorageError, ValueError):
    """A stored slot value was written by a newer declaration than the reader supports."""


class SlotCodec[ValueT](Protocol):
    """Encode values as strings and decode them using the version that wrote them.

    The decoder handles migrations from older versions. The store rejects values newer
    than the reading slot declaration before calling the decoder.
    """

    def encode(self, value: ValueT) -> str: ...

    def decode(self, payload: str, version: int) -> ValueT: ...


@dataclass(frozen=True, slots=True)
class Slot[ScopeT: Hashable, ValueT]:
    """Declare a value stored under a slot name and application scope.

    The name identifies the slot independently of this declaration's object identity.
    TTL defaults to no expiry; a configured duration and version must be positive.
    """

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
    """Store encoded values by slot name and application scope.

    Get returns None for missing or expired values, so a stored None is indistinguishable
    from absence. Touch extends a live value by the TTL saved with that value. Expired
    rows remain until deleted, overwritten, or purged.

    Put uses the slot's default TTL when its ttl argument is None and records the slot's
    current version. Decode may migrate older versions; newer versions raise SlotVersionError.
    Separate get and put calls do not provide atomic read-modify-write semantics.
    Delete reports whether a row existed, including an expired row; purge counts removed
    expired rows.
    """

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
    """Encode common scope types for the SQL stores.

    This convenience format can collide across types: the string "int:1" and integer 1
    have the same key. Applications mixing types or persisting custom objects should
    supply a stable, collision-free scope_encoder instead of relying on repr.
    """
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
    """Keep encoded slot values in memory, keyed by the actual hashable scope.

    Values still pass through the codec on every write and read, matching SQL storage.
    """

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

    Operations open their own connections and create the schema lazily. Hosts sharing
    one file need synchronized wall clocks for expiry. Supply a stable scope_encoder
    when scopes need a persistent format beyond the default type-based encoding.
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
    """Persist scoped values with PostgreSQL-owned expiry deadlines.

    The caller owns the asyncpg pool; the store creates its tables lazily. Scope keys
    use the same encoding rules as SQLiteScopedStore.
    """

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
                    CASE WHEN $5::double precision IS NULL THEN NULL
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
    """Encode JSON-compatible values, optionally decoding them into application types.

    A supplied decoder receives the parsed JSON and stored version. Without one, JSON
    values are returned as decoded and the version is ignored.
    """

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
