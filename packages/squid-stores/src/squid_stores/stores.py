"""Fenced durable-session store contracts and local implementations."""

import asyncio
import math
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Protocol, runtime_checkable

_SCHEMA_VERSION = 2
_LEGACY_SCHEMA_KEY = "__squid_layouts_schema_version__"
# The table keeps its original name: renaming the class is a source change, renaming a
# deployed table is a migration. Every message below that says "snapshot" is about this
# table or its schema, and is accurate.
_DEFAULT_TABLE_NAME = "squid_layout_snapshots"


@dataclass(frozen=True, slots=True)
class StoredSessionRecord:
    """One published durable session and its distributed-admission summary."""

    key: str
    scope: str
    summary_payload: str
    snapshot_payload: str


@dataclass(frozen=True, slots=True)
class ClaimToken:
    """Opaque proof of one record claim.

    Stores mint a strictly newer fence for every successful claim or admission
    commit. Callers must carry the token unchanged and must not interpret its
    private fence value.
    """

    key: str
    owner: str
    _fence: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class AdmissionToken:
    """Opaque proof of a short-lived exclusive reservation on one scope."""

    scope: str
    owner: str
    _fence: int = field(repr=False)


@runtime_checkable
class DurableSessionStore(Protocol):
    """Claim-fenced storage and distributed scope admission.

    Token-conditional operations fail after expiry, takeover, or retirement.
    ``inspect`` returns ``None`` for a lost reservation and an empty tuple for
    a valid empty scope. ``commit`` consumes a valid reservation, retires only
    named records in that reservation's scope, and publishes the claimed
    newcomer atomically. A target already present must be named as a victim.
    """

    async def list_records(self) -> tuple[StoredSessionRecord, ...]: ...

    async def load(self, key: str) -> StoredSessionRecord | None: ...

    async def claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None: ...

    async def renew(self, token: ClaimToken, lease_seconds: float) -> bool: ...

    async def save(self, token: ClaimToken, summary_payload: str, snapshot_payload: str) -> bool: ...

    async def delete(self, token: ClaimToken) -> bool: ...

    async def release(self, token: ClaimToken) -> bool: ...

    async def reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None: ...

    async def inspect(self, reservation: AdmissionToken) -> tuple[StoredSessionRecord, ...] | None: ...

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        summary_payload: str,
        snapshot_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None: ...

    async def abandon(self, reservation: AdmissionToken) -> bool: ...


@dataclass(slots=True)
class _MemoryLease:
    owner: str
    fence: int
    expires_at: float


class MemorySessionStore:
    """In-process implementation of the complete fenced store contract."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._records: dict[str, StoredSessionRecord] = {}
        self._claims: dict[str, _MemoryLease] = {}
        self._admissions: dict[str, _MemoryLease] = {}
        self._next_fence = 0
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list_records(self) -> tuple[StoredSessionRecord, ...]:
        async with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    async def load(self, key: str) -> StoredSessionRecord | None:
        _validate_key(key)
        async with self._lock:
            return self._records.get(key)

    async def claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None:
        _validate_key(key)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        async with self._lock:
            if key not in self._records:
                return None
            now = self._clock()
            current = self._claims.get(key)
            if current is not None and current.owner != owner and current.expires_at > now:
                return None
            fence = self._mint_fence()
            self._claims[key] = _MemoryLease(owner, fence, now + lease_seconds)
            return ClaimToken(key, owner, fence)

    async def renew(self, token: ClaimToken, lease_seconds: float) -> bool:
        _validate_lease_seconds(lease_seconds)
        async with self._lock:
            current = self._active_claim(token)
            if current is None:
                return False
            current.expires_at = self._clock() + lease_seconds
            return True

    async def save(self, token: ClaimToken, summary_payload: str, snapshot_payload: str) -> bool:
        async with self._lock:
            if self._active_claim(token) is None:
                return False
            record = self._records.get(token.key)
            if record is None:
                return False
            self._records[token.key] = StoredSessionRecord(token.key, record.scope, summary_payload, snapshot_payload)
            return True

    async def delete(self, token: ClaimToken) -> bool:
        async with self._lock:
            if self._active_claim(token) is None:
                return False
            self._records.pop(token.key, None)
            self._claims.pop(token.key, None)
            return True

    async def release(self, token: ClaimToken) -> bool:
        async with self._lock:
            if not self._matches(self._claims.get(token.key), token.owner, token._fence):
                return False
            self._claims.pop(token.key, None)
            return True

    async def reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None:
        _validate_scope(scope)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        async with self._lock:
            now = self._clock()
            current = self._admissions.get(scope)
            if current is not None and current.owner != owner and current.expires_at > now:
                return None
            fence = self._mint_fence()
            self._admissions[scope] = _MemoryLease(owner, fence, now + lease_seconds)
            return AdmissionToken(scope, owner, fence)

    async def inspect(self, reservation: AdmissionToken) -> tuple[StoredSessionRecord, ...] | None:
        async with self._lock:
            if self._active_admission(reservation) is None:
                return None
            return tuple(record for record in self._ordered_records() if record.scope == reservation.scope)

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        summary_payload: str,
        snapshot_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None:
        _validate_key(key)
        _validate_victims(victims)
        _validate_lease_seconds(lease_seconds)
        async with self._lock:
            if self._active_admission(reservation) is None:
                return None
            victim_keys = set(victims)
            if not self._valid_retirement(reservation.scope, key, victim_keys):
                return None
            for victim in victim_keys:
                self._records.pop(victim, None)
                self._claims.pop(victim, None)
            fence = self._mint_fence()
            self._records[key] = StoredSessionRecord(key, reservation.scope, summary_payload, snapshot_payload)
            self._claims[key] = _MemoryLease(reservation.owner, fence, self._clock() + lease_seconds)
            self._admissions.pop(reservation.scope, None)
            return ClaimToken(key, reservation.owner, fence)

    async def abandon(self, reservation: AdmissionToken) -> bool:
        async with self._lock:
            if not self._matches(self._admissions.get(reservation.scope), reservation.owner, reservation._fence):
                return False
            self._admissions.pop(reservation.scope, None)
            return True

    def _active_claim(self, token: ClaimToken) -> _MemoryLease | None:
        current = self._claims.get(token.key)
        if not self._matches(current, token.owner, token._fence):
            return None
        assert current is not None
        return current if current.expires_at > self._clock() else None

    def _active_admission(self, token: AdmissionToken) -> _MemoryLease | None:
        current = self._admissions.get(token.scope)
        if not self._matches(current, token.owner, token._fence):
            return None
        assert current is not None
        return current if current.expires_at > self._clock() else None

    def _valid_retirement(self, scope: str, key: str, victims: set[str]) -> bool:
        if self._records.get(key) is not None and key not in victims:
            return False
        return all((record := self._records.get(victim)) is None or record.scope == scope for victim in victims)

    def _ordered_records(self) -> tuple[StoredSessionRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def _mint_fence(self) -> int:
        self._next_fence += 1
        return self._next_fence

    @staticmethod
    def _matches(current: _MemoryLease | None, owner: str, fence: int) -> bool:
        return current is not None and current.owner == owner and current.fence == fence


class SQLiteSessionStore:
    """Persist fenced durable sessions in a SQLite file.

    SQLite is a single-host/shared-filesystem option. Lease expiry uses the
    supplied process wall clock, so every process accessing one file must share
    a sufficiently synchronized clock.

    Args:
        path: Database file path. Parent directories must already exist.
        table_name: Unqualified database table name. Derived helper tables use
            the same prefix.
        clock: Wall clock used for lease and reservation expiry.
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
        self._metadata_table = f"{self.table_name}_metadata"
        self._admissions_table = f"{self.table_name}_admissions"
        self._clock = clock
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def list_records(self) -> tuple[StoredSessionRecord, ...]:
        await self._initialize()
        return await asyncio.to_thread(self._list_records)

    async def load(self, key: str) -> StoredSessionRecord | None:
        _validate_key(key)
        await self._initialize()
        return await asyncio.to_thread(self._load, key)

    async def claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None:
        _validate_key(key)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        return await asyncio.to_thread(self._claim, key, owner, lease_seconds)

    async def renew(self, token: ClaimToken, lease_seconds: float) -> bool:
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        return await asyncio.to_thread(self._renew, token, lease_seconds)

    async def save(self, token: ClaimToken, summary_payload: str, snapshot_payload: str) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._save, token, summary_payload, snapshot_payload)

    async def delete(self, token: ClaimToken) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._delete, token)

    async def release(self, token: ClaimToken) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._release, token)

    async def reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None:
        _validate_scope(scope)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        return await asyncio.to_thread(self._reserve, scope, owner, lease_seconds)

    async def inspect(self, reservation: AdmissionToken) -> tuple[StoredSessionRecord, ...] | None:
        await self._initialize()
        return await asyncio.to_thread(self._inspect, reservation)

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        summary_payload: str,
        snapshot_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None:
        _validate_key(key)
        _validate_victims(victims)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        return await asyncio.to_thread(
            self._commit, reservation, key, summary_payload, snapshot_payload, victims, lease_seconds
        )

    async def abandon(self, reservation: AdmissionToken) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._abandon, reservation)

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
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    key TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    summary_payload TEXT NOT NULL,
                    snapshot_payload TEXT NOT NULL,
                    claim_owner TEXT,
                    claim_fence INTEGER,
                    lease_until REAL,
                    CHECK (
                        (claim_owner IS NULL AND claim_fence IS NULL AND lease_until IS NULL)
                        OR (claim_owner IS NOT NULL AND claim_fence IS NOT NULL AND lease_until IS NOT NULL)
                    )
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({self.table_name})")}
            if "payload" in columns:
                self._migrate_v1(connection)
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table_name}_scope_idx ON {self.table_name} (scope, key)"
            )
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {self._metadata_table} (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._admissions_table} (
                    scope TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    fence INTEGER NOT NULL,
                    lease_until REAL NOT NULL
                )
                """
            )
            row = connection.execute(
                f"SELECT value FROM {self._metadata_table} WHERE name = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {self._metadata_table} (name, value) VALUES ('schema_version', ?)",
                    (_SCHEMA_VERSION,),
                )
            else:
                _check_schema_version(str(row[0]))
            maximum_fence = connection.execute(
                f"SELECT COALESCE(MAX(claim_fence), 0) FROM {self.table_name}"
            ).fetchone()[0]
            connection.execute(
                f"""
                INSERT INTO {self._metadata_table} (name, value) VALUES ('next_fence', ?)
                ON CONFLICT(name) DO UPDATE SET value = MAX(value, excluded.value)
                """,
                (maximum_fence,),
            )
            connection.commit()

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        version = connection.execute(
            f"SELECT payload FROM {self.table_name} WHERE key = ?", (_LEGACY_SCHEMA_KEY,)
        ).fetchone()
        if version is None or str(version[0]) != "1":
            message = "legacy snapshot store schema version is missing or malformed"
            raise RuntimeError(message)
        replacement = f"{self.table_name}_v2"
        connection.execute(f"DROP TABLE IF EXISTS {replacement}")
        connection.execute(
            f"""
            CREATE TABLE {replacement} (
                key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                summary_payload TEXT NOT NULL,
                snapshot_payload TEXT NOT NULL,
                claim_owner TEXT,
                claim_fence INTEGER,
                lease_until REAL,
                CHECK (
                    (claim_owner IS NULL AND claim_fence IS NULL AND lease_until IS NULL)
                    OR (claim_owner IS NOT NULL AND claim_fence IS NOT NULL AND lease_until IS NOT NULL)
                )
            )
            """
        )
        rows = connection.execute(
            f"SELECT key, payload, owner, lease_until FROM {self.table_name} WHERE key <> ? ORDER BY key",
            (_LEGACY_SCHEMA_KEY,),
        ).fetchall()
        fence = 0
        for key, payload, owner, lease_until in rows:
            claim_fence = None
            if owner is not None and lease_until is not None:
                fence += 1
                claim_fence = fence
            connection.execute(
                f"""
                INSERT INTO {replacement}
                    (key, scope, summary_payload, snapshot_payload, claim_owner, claim_fence, lease_until)
                VALUES (?, ?, '', ?, ?, ?, ?)
                """,
                (key, key, payload, owner, claim_fence, lease_until),
            )
        connection.execute(f"DROP TABLE {self.table_name}")
        connection.execute(f"ALTER TABLE {replacement} RENAME TO {self.table_name}")

    def _list_records(self) -> tuple[StoredSessionRecord, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT key, scope, summary_payload, snapshot_payload FROM {self.table_name} ORDER BY key"
            ).fetchall()
        return tuple(StoredSessionRecord(*(str(value) for value in row)) for row in rows)

    def _load(self, key: str) -> StoredSessionRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT key, scope, summary_payload, snapshot_payload FROM {self.table_name} WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else StoredSessionRecord(*(str(value) for value in row))

    def _claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self._clock()
            row = connection.execute(
                f"SELECT claim_owner, lease_until FROM {self.table_name} WHERE key = ?", (key,)
            ).fetchone()
            if row is None or (row[0] != owner and row[1] is not None and float(row[1]) > now):
                connection.rollback()
                return None
            fence = self._mint_fence(connection)
            connection.execute(
                f"UPDATE {self.table_name} SET claim_owner = ?, claim_fence = ?, lease_until = ? WHERE key = ?",
                (owner, fence, now + lease_seconds, key),
            )
            connection.commit()
        return ClaimToken(key, owner, fence)

    def _renew(self, token: ClaimToken, lease_seconds: float) -> bool:
        with closing(self._connect()) as connection, connection:
            now = self._clock()
            cursor = connection.execute(
                f"""
                UPDATE {self.table_name} SET lease_until = ?
                WHERE key = ? AND claim_owner = ? AND claim_fence = ? AND lease_until > ?
                """,
                (now + lease_seconds, token.key, token.owner, token._fence, now),
            )
        return cursor.rowcount == 1

    def _save(self, token: ClaimToken, summary_payload: str, snapshot_payload: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table_name} SET summary_payload = ?, snapshot_payload = ?
                WHERE key = ? AND claim_owner = ? AND claim_fence = ? AND lease_until > ?
                """,
                (summary_payload, snapshot_payload, token.key, token.owner, token._fence, self._clock()),
            )
        return cursor.rowcount == 1

    def _delete(self, token: ClaimToken) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                DELETE FROM {self.table_name}
                WHERE key = ? AND claim_owner = ? AND claim_fence = ? AND lease_until > ?
                """,
                (token.key, token.owner, token._fence, self._clock()),
            )
        return cursor.rowcount == 1

    def _release(self, token: ClaimToken) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table_name} SET claim_owner = NULL, claim_fence = NULL, lease_until = NULL
                WHERE key = ? AND claim_owner = ? AND claim_fence = ?
                """,
                (token.key, token.owner, token._fence),
            )
        return cursor.rowcount == 1

    def _reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self._clock()
            current = connection.execute(
                f"SELECT owner, lease_until FROM {self._admissions_table} WHERE scope = ?", (scope,)
            ).fetchone()
            if current is not None and current[0] != owner and float(current[1]) > now:
                connection.rollback()
                return None
            fence = self._mint_fence(connection)
            connection.execute(
                f"""
                INSERT INTO {self._admissions_table} (scope, owner, fence, lease_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    owner = excluded.owner, fence = excluded.fence, lease_until = excluded.lease_until
                """,
                (scope, owner, fence, now + lease_seconds),
            )
            connection.commit()
        return AdmissionToken(scope, owner, fence)

    def _inspect(self, reservation: AdmissionToken) -> tuple[StoredSessionRecord, ...] | None:
        with closing(self._connect()) as connection:
            valid = connection.execute(
                f"""
                SELECT TRUE FROM {self._admissions_table}
                WHERE scope = ? AND owner = ? AND fence = ? AND lease_until > ?
                """,
                (reservation.scope, reservation.owner, reservation._fence, self._clock()),
            ).fetchone()
            if valid is None:
                return None
            rows = connection.execute(
                f"SELECT key, scope, summary_payload, snapshot_payload FROM {self.table_name} WHERE scope = ? ORDER BY key",
                (reservation.scope,),
            ).fetchall()
        return tuple(StoredSessionRecord(*(str(value) for value in row)) for row in rows)

    def _commit(
        self,
        reservation: AdmissionToken,
        key: str,
        summary_payload: str,
        snapshot_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self._clock()
            valid = connection.execute(
                f"""
                SELECT TRUE FROM {self._admissions_table}
                WHERE scope = ? AND owner = ? AND fence = ? AND lease_until > ?
                """,
                (reservation.scope, reservation.owner, reservation._fence, now),
            ).fetchone()
            if valid is None or not self._retirement_is_valid(connection, reservation.scope, key, victims):
                connection.rollback()
                return None
            if victims:
                placeholders = ", ".join("?" for _ in victims)
                connection.execute(
                    f"DELETE FROM {self.table_name} WHERE scope = ? AND key IN ({placeholders})",
                    (reservation.scope, *victims),
                )
            fence = self._mint_fence(connection)
            connection.execute(
                f"""
                INSERT INTO {self.table_name}
                    (key, scope, summary_payload, snapshot_payload, claim_owner, claim_fence, lease_until)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    reservation.scope,
                    summary_payload,
                    snapshot_payload,
                    reservation.owner,
                    fence,
                    now + lease_seconds,
                ),
            )
            connection.execute(
                f"DELETE FROM {self._admissions_table} WHERE scope = ? AND owner = ? AND fence = ?",
                (reservation.scope, reservation.owner, reservation._fence),
            )
            connection.commit()
        return ClaimToken(key, reservation.owner, fence)

    def _retirement_is_valid(
        self, connection: sqlite3.Connection, scope: str, key: str, victims: tuple[str, ...]
    ) -> bool:
        victim_keys = set(victims)
        existing = connection.execute(f"SELECT scope FROM {self.table_name} WHERE key = ?", (key,)).fetchone()
        if existing is not None and key not in victim_keys:
            return False
        if not victims:
            return True
        placeholders = ", ".join("?" for _ in victims)
        wrong_scope = connection.execute(
            f"SELECT TRUE FROM {self.table_name} WHERE key IN ({placeholders}) AND scope <> ? LIMIT 1",
            (*victims, scope),
        ).fetchone()
        return wrong_scope is None

    def _abandon(self, reservation: AdmissionToken) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"DELETE FROM {self._admissions_table} WHERE scope = ? AND owner = ? AND fence = ?",
                (reservation.scope, reservation.owner, reservation._fence),
            )
        return cursor.rowcount == 1

    def _mint_fence(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            f"UPDATE {self._metadata_table} SET value = value + 1 WHERE name = 'next_fence' RETURNING value"
        ).fetchone()
        if row is None:
            message = "snapshot store fence counter is missing"
            raise RuntimeError(message)
        return int(row[0])


def _validate_table_name(table_name: str) -> str:
    if len(table_name) > 48 or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
        message = "snapshot table name must be an unqualified SQL identifier of at most 48 characters"
        raise ValueError(message)
    return table_name


def _validate_key(key: str) -> None:
    if not key:
        message = "durable session key must not be empty"
        raise ValueError(message)


def _validate_scope(scope: str) -> None:
    if not scope:
        message = "durable session scope must not be empty"
        raise ValueError(message)


def _validate_owner(owner: str) -> None:
    if not owner:
        message = "durable session owner must not be empty"
        raise ValueError(message)


def _validate_lease_seconds(lease_seconds: float) -> None:
    if not math.isfinite(lease_seconds) or lease_seconds <= 0:
        message = "durable session lease must be a finite positive duration"
        raise ValueError(message)


def _validate_victims(victims: tuple[str, ...]) -> None:
    if len(victims) != len(set(victims)):
        message = "durable session retirement victims must be unique"
        raise ValueError(message)
    for victim in victims:
        _validate_key(victim)


def _check_schema_version(raw: str) -> None:
    try:
        version = int(raw)
    except ValueError as error:
        message = "snapshot store schema version is malformed"
        raise RuntimeError(message) from error
    if version != _SCHEMA_VERSION:
        message = f"snapshot store schema {version} does not match supported version {_SCHEMA_VERSION}"
        raise RuntimeError(message)
