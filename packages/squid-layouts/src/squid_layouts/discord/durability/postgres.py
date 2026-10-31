"""Optional asyncpg fenced durable-session store."""

import asyncio
from typing import TYPE_CHECKING

from .stores import (
    _LEGACY_SCHEMA_KEY,
    _SCHEMA_VERSION,
    AdmissionToken,
    ClaimToken,
    StoredSessionRecord,
    _check_schema_version,
    _validate_key,
    _validate_lease_seconds,
    _validate_owner,
    _validate_scope,
    _validate_table_name,
    _validate_victims,
)

if TYPE_CHECKING:
    from asyncpg import Connection, Pool, Record
else:
    try:
        from asyncpg import Connection, Pool, Record
    except ModuleNotFoundError:
        Connection = Pool = Record = object


_DEFAULT_TABLE_NAME = "squid_layout_snapshots"


class PostgresSnapshotStore:
    """Persist fenced durable sessions through an asyncpg pool.

    PostgreSQL is the multi-host store. Every expiry comparison and every new
    deadline uses ``clock_timestamp()`` in the database; hosts supply lease
    durations, never absolute timestamps, so process clock skew cannot create
    overlapping owners. Claim and admission fences come from one PostgreSQL
    sequence and therefore increase monotonically across all callers.

    Args:
        pool: An asyncpg connection pool.
        table_name: Unqualified database table name. Derived helper objects use
            the same prefix.
    """

    def __init__(self, pool: Pool, *, table_name: str = _DEFAULT_TABLE_NAME) -> None:
        self.pool = pool
        self.table_name = _validate_table_name(table_name)
        self._metadata_table = f"{self.table_name}_metadata"
        self._admissions_table = f"{self.table_name}_admissions"
        self._fence_sequence = f"{self.table_name}_fence_seq"
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def list_records(self) -> tuple[StoredSessionRecord, ...]:
        await self._initialize()
        rows = await self.pool.fetch(
            f"SELECT key, scope, summary_payload, snapshot_payload FROM {self.table_name} ORDER BY key"
        )
        return tuple(_stored_record(row) for row in rows)

    async def load(self, key: str) -> StoredSessionRecord | None:
        _validate_key(key)
        await self._initialize()
        row = await self.pool.fetchrow(
            f"SELECT key, scope, summary_payload, snapshot_payload FROM {self.table_name} WHERE key = $1",
            key,
        )
        return None if row is None else _stored_record(row)

    async def claim(self, key: str, owner: str, lease_seconds: float) -> ClaimToken | None:
        _validate_key(key)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        fence = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name}
            SET claim_owner = $2,
                claim_fence = nextval('{self._fence_sequence}'),
                lease_until = clock_timestamp() + ($3::double precision * INTERVAL '1 second')
            WHERE key = $1
              AND (claim_owner = $2 OR lease_until IS NULL OR lease_until <= clock_timestamp())
            RETURNING claim_fence
            """,
            key,
            owner,
            lease_seconds,
        )
        return None if fence is None else ClaimToken(key, owner, int(fence))

    async def renew(self, token: ClaimToken, lease_seconds: float) -> bool:
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        renewed = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name}
            SET lease_until = clock_timestamp() + ($4::double precision * INTERVAL '1 second')
            WHERE key = $1 AND claim_owner = $2 AND claim_fence = $3
              AND lease_until > clock_timestamp()
            RETURNING TRUE
            """,
            token.key,
            token.owner,
            token._fence,
            lease_seconds,
        )
        return renewed is True

    async def save(self, token: ClaimToken, summary_payload: str, snapshot_payload: str) -> bool:
        await self._initialize()
        saved = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name} SET summary_payload = $4, snapshot_payload = $5
            WHERE key = $1 AND claim_owner = $2 AND claim_fence = $3
              AND lease_until > clock_timestamp()
            RETURNING TRUE
            """,
            token.key,
            token.owner,
            token._fence,
            summary_payload,
            snapshot_payload,
        )
        return saved is True

    async def delete(self, token: ClaimToken) -> bool:
        await self._initialize()
        deleted = await self.pool.fetchval(
            f"""
            DELETE FROM {self.table_name}
            WHERE key = $1 AND claim_owner = $2 AND claim_fence = $3
              AND lease_until > clock_timestamp()
            RETURNING TRUE
            """,
            token.key,
            token.owner,
            token._fence,
        )
        return deleted is True

    async def release(self, token: ClaimToken) -> bool:
        await self._initialize()
        released = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name}
            SET claim_owner = NULL, claim_fence = NULL, lease_until = NULL
            WHERE key = $1 AND claim_owner = $2 AND claim_fence = $3
            RETURNING TRUE
            """,
            token.key,
            token.owner,
            token._fence,
        )
        return released is True

    async def reserve(self, scope: str, owner: str, lease_seconds: float) -> AdmissionToken | None:
        _validate_scope(scope)
        _validate_owner(owner)
        _validate_lease_seconds(lease_seconds)
        await self._initialize()
        fence = await self.pool.fetchval(
            f"""
            INSERT INTO {self._admissions_table} (scope, owner, fence, lease_until)
            VALUES (
                $1, $2, nextval('{self._fence_sequence}'),
                clock_timestamp() + ($3::double precision * INTERVAL '1 second')
            )
            ON CONFLICT(scope) DO UPDATE SET
                owner = excluded.owner,
                fence = nextval('{self._fence_sequence}'),
                lease_until = clock_timestamp() + ($3::double precision * INTERVAL '1 second')
            WHERE {self._admissions_table}.owner = $2
               OR {self._admissions_table}.lease_until <= clock_timestamp()
            RETURNING fence
            """,
            scope,
            owner,
            lease_seconds,
        )
        return None if fence is None else AdmissionToken(scope, owner, int(fence))

    async def inspect(self, reservation: AdmissionToken) -> tuple[StoredSessionRecord, ...] | None:
        await self._initialize()
        async with self.pool.acquire() as connection, connection.transaction():
            valid = await connection.fetchval(
                f"""
                SELECT TRUE FROM {self._admissions_table}
                WHERE scope = $1 AND owner = $2 AND fence = $3
                  AND lease_until > clock_timestamp()
                """,
                reservation.scope,
                reservation.owner,
                reservation._fence,
            )
            if valid is not True:
                return None
            rows = await connection.fetch(
                f"""
                SELECT key, scope, summary_payload, snapshot_payload
                FROM {self.table_name} WHERE scope = $1 ORDER BY key
                """,
                reservation.scope,
            )
        return tuple(_stored_record(row) for row in rows)

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
        async with self.pool.acquire() as connection, connection.transaction():
            valid = await connection.fetchval(
                f"""
                SELECT TRUE FROM {self._admissions_table}
                WHERE scope = $1 AND owner = $2 AND fence = $3
                  AND lease_until > clock_timestamp()
                FOR UPDATE
                """,
                reservation.scope,
                reservation.owner,
                reservation._fence,
            )
            if valid is not True or not await self._retirement_is_valid(connection, reservation.scope, key, victims):
                return None
            if victims:
                await connection.execute(
                    f"DELETE FROM {self.table_name} WHERE scope = $1 AND key = ANY($2::text[])",
                    reservation.scope,
                    list(victims),
                )
            fence = await connection.fetchval(f"SELECT nextval('{self._fence_sequence}')")
            await connection.execute(
                f"""
                INSERT INTO {self.table_name}
                    (key, scope, summary_payload, snapshot_payload, claim_owner, claim_fence, lease_until)
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    clock_timestamp() + ($7::double precision * INTERVAL '1 second')
                )
                """,
                key,
                reservation.scope,
                summary_payload,
                snapshot_payload,
                reservation.owner,
                fence,
                lease_seconds,
            )
            await connection.execute(
                f"DELETE FROM {self._admissions_table} WHERE scope = $1 AND owner = $2 AND fence = $3",
                reservation.scope,
                reservation.owner,
                reservation._fence,
            )
        return ClaimToken(key, reservation.owner, int(fence))

    async def abandon(self, reservation: AdmissionToken) -> bool:
        await self._initialize()
        abandoned = await self.pool.fetchval(
            f"""
            DELETE FROM {self._admissions_table}
            WHERE scope = $1 AND owner = $2 AND fence = $3
            RETURNING TRUE
            """,
            reservation.scope,
            reservation.owner,
            reservation._fence,
        )
        return abandoned is True

    async def _retirement_is_valid(
        self, connection: Connection, scope: str, key: str, victims: tuple[str, ...]
    ) -> bool:
        existing_scope = await connection.fetchval(
            f"SELECT scope FROM {self.table_name} WHERE key = $1 FOR UPDATE", key
        )
        if existing_scope is not None and key not in victims:
            return False
        if not victims:
            return True
        wrong_scope = await connection.fetchval(
            f"""
            SELECT TRUE FROM {self.table_name}
            WHERE key = ANY($1::text[]) AND scope <> $2
            LIMIT 1
            FOR UPDATE
            """,
            list(victims),
            scope,
        )
        return wrong_scope is None

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            async with self.pool.acquire() as connection, connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", self.table_name)
                await connection.execute(f"CREATE SEQUENCE IF NOT EXISTS {self._fence_sequence}")
                await connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        key TEXT PRIMARY KEY,
                        scope TEXT NOT NULL,
                        summary_payload TEXT NOT NULL,
                        snapshot_payload TEXT NOT NULL,
                        claim_owner TEXT,
                        claim_fence BIGINT,
                        lease_until TIMESTAMPTZ,
                        CHECK (
                            (claim_owner IS NULL AND claim_fence IS NULL AND lease_until IS NULL)
                            OR (claim_owner IS NOT NULL AND claim_fence IS NOT NULL AND lease_until IS NOT NULL)
                        )
                    )
                    """
                )
                columns = await connection.fetch(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = $1
                    """,
                    self.table_name.lower(),
                )
                if "payload" in {str(row["column_name"]) for row in columns}:
                    await self._migrate_v1(connection)
                await connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.table_name}_scope_idx ON {self.table_name} (scope, key)"
                )
                await connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._metadata_table} (name TEXT PRIMARY KEY, value BIGINT NOT NULL)"
                )
                await connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._admissions_table} (
                        scope TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        fence BIGINT NOT NULL,
                        lease_until TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                raw_version = await connection.fetchval(
                    f"SELECT value FROM {self._metadata_table} WHERE name = 'schema_version'"
                )
                if raw_version is None:
                    await connection.execute(
                        f"INSERT INTO {self._metadata_table} (name, value) VALUES ('schema_version', $1)",
                        _SCHEMA_VERSION,
                    )
                else:
                    _check_schema_version(str(raw_version))
            self._initialized = True

    async def _migrate_v1(self, connection: Connection) -> None:
        version = await connection.fetchval(f"SELECT payload FROM {self.table_name} WHERE key = $1", _LEGACY_SCHEMA_KEY)
        if version is None or str(version) != "1":
            message = "legacy snapshot store schema version is missing or malformed"
            raise RuntimeError(message)
        await connection.execute(f"DELETE FROM {self.table_name} WHERE key = $1", _LEGACY_SCHEMA_KEY)
        await connection.execute(
            f"""
            ALTER TABLE {self.table_name}
                ADD COLUMN scope TEXT,
                ADD COLUMN summary_payload TEXT,
                ADD COLUMN snapshot_payload TEXT,
                ADD COLUMN claim_fence BIGINT
            """
        )
        await connection.execute(
            f"""
            UPDATE {self.table_name}
            SET scope = key,
                summary_payload = '',
                snapshot_payload = payload,
                claim_fence = CASE
                    WHEN owner IS NOT NULL AND lease_until IS NOT NULL
                    THEN nextval('{self._fence_sequence}')
                END
            """
        )
        await connection.execute(
            f"""
            ALTER TABLE {self.table_name}
                ALTER COLUMN lease_until TYPE TIMESTAMPTZ USING to_timestamp(lease_until),
                ALTER COLUMN scope SET NOT NULL,
                ALTER COLUMN summary_payload SET NOT NULL,
                ALTER COLUMN snapshot_payload SET NOT NULL,
                DROP COLUMN payload
            """
        )
        await connection.execute(f"ALTER TABLE {self.table_name} RENAME COLUMN owner TO claim_owner")
        await connection.execute(
            f"""
            ALTER TABLE {self.table_name} ADD CONSTRAINT {self.table_name}_claim_shape_ck CHECK (
                (claim_owner IS NULL AND claim_fence IS NULL AND lease_until IS NULL)
                OR (claim_owner IS NOT NULL AND claim_fence IS NOT NULL AND lease_until IS NOT NULL)
            )
            """
        )


def _stored_record(row: Record) -> StoredSessionRecord:
    return StoredSessionRecord(
        key=str(row["key"]),
        scope=str(row["scope"]),
        summary_payload=str(row["summary_payload"]),
        snapshot_payload=str(row["snapshot_payload"]),
    )
