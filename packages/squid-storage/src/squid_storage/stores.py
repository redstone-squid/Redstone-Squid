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

_SCHEMA_VERSION = 1
_DEFAULT_TABLE_NAME = "squid_sessions"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One published durable session; the claim on it, if any, is not part of the record."""

    key: str
    scope: str
    snapshot_payload: str
    """Opaque text `inspect` returns for every record in a scope; admission decisions read it in bulk."""
    record_payload: str
    """Opaque text a recovering host reads once it holds the claim; the store never parses either payload."""


@dataclass(frozen=True, slots=True)
class ClaimToken:
    """Proof of one claim on one record, valid until the lease lapses or a newer fence replaces it.

    Stores mint a strictly newer fence for every successful `claim` or `commit`, so a token
    stops matching as soon as anyone, including its own owner, claims the key again.
    Carry it unchanged; the fence is private.
    """

    key: str
    owner: str
    _fence: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class AdmissionToken:
    """Proof of one reservation on one scope, consumed by `commit` or `abandon` and lost on expiry."""

    scope: str
    owner: str
    _fence: int = field(repr=False)


@runtime_checkable
class DurableSessionStore(Protocol):
    """Claim-fenced session records plus one exclusive reservation per scope.

    A claim ends when its lease expires unrenewed, when `release()` or `delete()` consumes
    it, or when a later `claim()` or `commit()` on the same key mints a newer fence; a
    reservation ends on expiry, `commit()`, `abandon()`, or a newer `reserve()` on the
    scope. Every token-conditional method then reports the loss as `False` or `None`
    instead of raising. Fences increase strictly per store, so a stale token never
    matches again.

    Every implementation raises `ValueError`, before touching storage, for an empty key,
    scope or owner, a non-finite or non-positive lease, or duplicate victims.
    """

    async def list(self) -> tuple[SessionRecord, ...]:
        """Return every record in every scope, ordered by key, whoever holds its claim."""
        ...

    async def load(self, key: str) -> SessionRecord | None:
        """Return the record under `key` regardless of claim state, or `None` when absent."""
        ...

    async def claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None:
        """Take or retake the claim on an existing record for `lease_seconds` from now.

        Returns `None` when no record has `key` or another owner's lease is still live. The
        same owner always succeeds, and the token it held before stops matching.
        """
        ...

    async def renew(self, token: ClaimToken, lease_seconds: float) -> bool:
        """Restart the lease from now; `False` once the claim is lost or the record is gone."""
        ...

    async def save(self, token: ClaimToken, snapshot_payload: str, record_payload: str) -> bool:
        """Replace both payloads, keeping the scope; `False` once the claim is lost."""
        ...

    async def delete(self, token: ClaimToken) -> bool:
        """Remove the record and its claim; `False` once the claim is lost."""
        ...

    async def release(self, token: ClaimToken) -> bool:
        """Drop the claim without touching the record.

        Unlike `renew`, an expired lease still releases as long as no newer fence has
        replaced the token; `False` only when it has, or the record is gone.
        """
        ...

    async def reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None:
        """Take or retake the reservation on `scope` for `lease_seconds` from now.

        Returns `None` while another owner's reservation is live. The same owner always
        succeeds, and the reservation it held before stops matching. A reservation blocks
        other reservations only; it does not block `claim` on records in the scope.
        """
        ...

    async def inspect(self, reservation: AdmissionToken) -> tuple[SessionRecord, ...] | None:
        """Return the scope's records ordered by key, or `None` once the reservation is lost."""
        ...

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        snapshot_payload: str,
        record_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None:
        """Atomically retire `victims`, publish `key` in the reservation's scope, and consume the reservation.

        The new record is claimed by the reservation's owner for `lease_seconds`, and every
        victim loses its record and its claim. A victim that does not exist is ignored.
        Returns `None`, changing nothing, when the reservation is lost, when `key` already
        exists and is not among `victims`, or when a victim exists in another scope.
        """
        ...

    async def abandon(self, reservation: AdmissionToken) -> bool:
        """Drop the reservation, expired or not; `False` once it was consumed or superseded."""
        ...


@dataclass(slots=True)
class _MemoryLease:
    owner: str
    fence: int
    expires_at: float


class MemorySessionStore:
    """`DurableSessionStore` over dicts: state dies with the process, and `clock` stands in for `time.time`."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._claims: dict[str, _MemoryLease] = {}
        self._admissions: dict[str, _MemoryLease] = {}
        self._next_fence = 0
        self._clock = clock
        self._lock = asyncio.Lock()

    async def list(self) -> tuple[SessionRecord, ...]:
        async with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    async def load(self, key: str) -> SessionRecord | None:
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

    async def save(self, token: ClaimToken, snapshot_payload: str, record_payload: str) -> bool:
        async with self._lock:
            if self._active_claim(token) is None:
                return False
            record = self._records.get(token.key)
            if record is None:
                return False
            self._records[token.key] = SessionRecord(token.key, record.scope, snapshot_payload, record_payload)
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

    async def inspect(self, reservation: AdmissionToken) -> tuple[SessionRecord, ...] | None:
        async with self._lock:
            if self._active_admission(reservation) is None:
                return None
            return tuple(record for record in self._ordered_records() if record.scope == reservation.scope)

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        snapshot_payload: str,
        record_payload: str,
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
            self._records[key] = SessionRecord(key, reservation.scope, snapshot_payload, record_payload)
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

    def _ordered_records(self) -> tuple[SessionRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def _mint_fence(self) -> int:
        self._next_fence += 1
        return self._next_fence

    @staticmethod
    def _matches(current: _MemoryLease | None, owner: str, fence: int) -> bool:
        return current is not None and current.owner == owner and current.fence == fence


class SQLiteSessionStore:
    """`DurableSessionStore` in a SQLite file, one short-lived WAL connection per call, off the event loop.

    SQLite is a single-host/shared-filesystem option. Lease expiry uses the
    supplied process wall clock, so every process accessing one file must share
    a sufficiently synchronized clock. The schema is created on the first call.

    Args:
        path: Database file path. Parent directories must already exist.
        table_name: Unqualified database table name. Derived helper tables use
            the same prefix.
        clock: Wall clock used for lease and reservation expiry.

    Raises:
        ValueError: From the constructor, for a table name that is not an identifier of
            at most 48 characters.
        RuntimeError: From the first call, when the file holds another schema version.
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

    async def list(self) -> tuple[SessionRecord, ...]:
        await self._initialize()
        return await asyncio.to_thread(self._list_records)

    async def load(self, key: str) -> SessionRecord | None:
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

    async def save(self, token: ClaimToken, snapshot_payload: str, record_payload: str) -> bool:
        await self._initialize()
        return await asyncio.to_thread(self._save, token, snapshot_payload, record_payload)

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

    async def inspect(self, reservation: AdmissionToken) -> tuple[SessionRecord, ...] | None:
        await self._initialize()
        return await asyncio.to_thread(self._inspect, reservation)

    async def commit(
        self,
        reservation: AdmissionToken,
        *,
        key: str,
        snapshot_payload: str,
        record_payload: str,
        victims: tuple[str, ...],
        lease_seconds: float,
    ) -> ClaimToken | None:
        _validate_key(key)
        _validate_victims(victims)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        return await asyncio.to_thread(
            self._commit, reservation, key, snapshot_payload, record_payload, victims, lease_seconds
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
                    snapshot_payload TEXT NOT NULL,
                    record_payload TEXT NOT NULL,
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

    def _list_records(self) -> tuple[SessionRecord, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT key, scope, snapshot_payload, record_payload FROM {self.table_name} ORDER BY key"
            ).fetchall()
        return tuple(SessionRecord(*(str(value) for value in row)) for row in rows)

    def _load(self, key: str) -> SessionRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT key, scope, snapshot_payload, record_payload FROM {self.table_name} WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else SessionRecord(*(str(value) for value in row))

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

    def _save(self, token: ClaimToken, snapshot_payload: str, record_payload: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table_name} SET snapshot_payload = ?, record_payload = ?
                WHERE key = ? AND claim_owner = ? AND claim_fence = ? AND lease_until > ?
                """,
                (snapshot_payload, record_payload, token.key, token.owner, token._fence, self._clock()),
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

    def _inspect(self, reservation: AdmissionToken) -> tuple[SessionRecord, ...] | None:
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
                f"SELECT key, scope, snapshot_payload, record_payload FROM {self.table_name} WHERE scope = ? ORDER BY key",
                (reservation.scope,),
            ).fetchall()
        return tuple(SessionRecord(*(str(value) for value in row)) for row in rows)

    def _commit(
        self,
        reservation: AdmissionToken,
        key: str,
        snapshot_payload: str,
        record_payload: str,
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
                    (key, scope, snapshot_payload, record_payload, claim_owner, claim_fence, lease_until)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    reservation.scope,
                    snapshot_payload,
                    record_payload,
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
            message = "session store fence counter is missing"
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
        message = "session store schema version is malformed"
        raise RuntimeError(message) from error
    if version != _SCHEMA_VERSION:
        message = f"session store schema {version} does not match supported version {_SCHEMA_VERSION}"
        raise RuntimeError(message)
