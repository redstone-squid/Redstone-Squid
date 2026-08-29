"""Optional asyncpg adapters: the fenced durable-session store and the topic bridge."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import anyio

from squid_reactive.topics import Address, KindKeyCodec, Topic, TopicBus, TopicCodec
from squid_stores.stores import (
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


logger = logging.getLogger(__name__)


class _NotifyConnection(Protocol):
    """The small asyncpg connection surface needed for transactional notifications."""

    async def execute(self, query: str, *args: object) -> object: ...


# The table keeps its original name: renaming the class is a source change, renaming a
# deployed table is a migration. Every message below that says "snapshot" is about this
# table or its schema, and is accurate.
_DEFAULT_TABLE_NAME = "squid_layout_snapshots"
_DEFAULT_TOPIC_CHANNEL = "squid_topics"
_ORIGIN_SEPARATOR = ":"
_NORMAL_DELIVERY = "normal"
_TRANSACTION_DELIVERY = "transaction"
_MAX_NOTIFY_PAYLOAD = 8000
"""PostgreSQL refuses a NOTIFY payload of 8000 bytes or more, counting the terminator."""


class PostgresSessionStore:
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


@dataclass(frozen=True, slots=True)
class TopicBridgeSnapshot:
    """One immutable diagnostic view of a cross-process topic bridge."""

    origin: str
    channel: str
    published: int
    local_only: int
    notified: int
    undelivered: int
    received: int
    ignored: int
    undecodable: int


class PostgresTopicBridge:
    """Carry encodable host topics between processes over PostgreSQL LISTEN/NOTIFY.

    The bridge is one more caller of `TopicBus.publish`, never a relay attached to the bus:
    a remote notification becomes a local publish, so the bus contract composes unchanged,
    and nothing loops back out. The payload is an encoded *address* and never application
    state, so subscribers still re-read their source of truth.

    Delivery is exactly as durable as the bus itself, which is to say not at all. NOTIFY is
    delivered only to processes listening at commit time, so a restart, a dropped connection
    or a full outbound queue costs latency rather than correctness; every consumer must still
    have a path that converges without a notification.

    Args:
        pool: An asyncpg connection pool. `run` holds one connection for the whole process.
        bus: The local bus that receives remote publishes and this host's own.
        codec: The wire form for this channel. Defaults to `kind:key`, which carries
            every `Topic`; supply one only to speak a format someone else defined.
        channel: PostgreSQL channel name shared by every process in the deployment.
        on_resync: Awaited after each successful (re)connection, for hosts that want to
            republish their coarse topics once notifications may have been missed.
        reconnect_seconds: Delay before rebuilding a lost listener connection.
        queue_size: Outbound notifications held while the sender is busy or stopped.
        origin: This process's identity, used to drop its own notifications. Defaults to a
            fresh UUID, which is what every process should use.
    """

    def __init__(
        self,
        pool: Pool,
        bus: TopicBus,
        codec: TopicCodec | None = None,
        *,
        channel: str = _DEFAULT_TOPIC_CHANNEL,
        on_resync: Callable[[], Awaitable[None]] | None = None,
        reconnect_seconds: float = 5.0,
        queue_size: int = 1024,
        origin: str | None = None,
    ) -> None:
        if not channel:
            message = "topic bridge channel must be a non-empty name"
            raise ValueError(message)
        if queue_size < 1:
            message = "topic bridge queue size must be at least one"
            raise ValueError(message)
        if origin is not None and _ORIGIN_SEPARATOR in origin:
            message = f"topic bridge origin cannot contain {_ORIGIN_SEPARATOR!r}"
            raise ValueError(message)
        self.pool = pool
        self.bus = bus
        self.codec: TopicCodec = KindKeyCodec() if codec is None else codec
        self.channel = channel
        self.on_resync = on_resync
        self.reconnect_seconds = reconnect_seconds
        self.origin = origin or uuid.uuid4().hex
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._pending: set[str] = set()
        self._listener_ready = asyncio.Event()
        self._running = False
        self._published = 0
        self._local_only = 0
        self._notified = 0
        self._undelivered = 0
        self._received = 0
        self._ignored = 0
        self._undecodable = 0

    def publish(self, *addresses: Address) -> None:
        """Publish on the local bus now, and queue a notification for the named topics.

        Synchronous like `TopicBus.publish`, and with the same guarantee for local
        subscribers. The notification leaves on the bridge's own connection shortly after
        this returns, so it is *not* ordered against a write the caller has not committed
        yet. Use `publish_in()` when the notification must be ordered with an application
        transaction.
        """
        self.bus.publish(*addresses)
        for address in addresses:
            self._published += 1
            # A `CellAddress` names a live object, so it has no wire form to look for.
            payload = self._wire_payload(address, _NORMAL_DELIVERY) if isinstance(address, Topic) else None
            if payload is None:
                self._local_only += 1
                continue
            if payload in self._pending:
                continue
            try:
                self._queue.put_nowait(payload)
            except asyncio.QueueFull:
                self._undelivered += 1
                logger.warning("topic bridge outbound queue is full; dropped a notification for %r", address)
                continue
            self._pending.add(payload)

    async def publish_in(self, connection: _NotifyConnection, topic: Topic) -> None:
        """Publish `topic` when the supplied PostgreSQL transaction commits.

        The supplied connection owns the `pg_notify` call, so PostgreSQL holds the
        notification until its current transaction commits. The bridge listener then
        publishes it to this process's bus as well as to listeners in other processes.
        This method does not commit the transaction.

        Raises:
            RuntimeError: If the bridge has not been started with :meth:`run`.
            ValueError: If the topic cannot fit on the configured NOTIFY channel.
        """
        payload = self._wire_payload(topic, _TRANSACTION_DELIVERY)
        if payload is None:
            message = f"topic {topic!r} cannot be carried over PostgreSQL NOTIFY"
            raise ValueError(message)
        if not self._running:
            message = "topic bridge must be running before publish_in()"
            raise RuntimeError(message)
        await self._listener_ready.wait()
        if not self._running:
            message = "topic bridge stopped before publish_in() could notify"
            raise RuntimeError(message)
        await connection.execute("SELECT pg_notify($1, $2)", self.channel, payload)
        self._notified += 1

    def _wire_payload(self, topic: Topic, delivery: str) -> str | None:
        """Return a tagged wire payload, or `None` when the topic cannot be carried."""
        encoded = self.codec.encode(topic)
        if encoded is None:
            return None
        payload = f"{self.origin}{_ORIGIN_SEPARATOR}{delivery}{_ORIGIN_SEPARATOR}{encoded}"
        if len(payload.encode()) >= _MAX_NOTIFY_PAYLOAD:
            logger.warning("topic bridge payload for %r exceeds the %d byte NOTIFY limit", topic, _MAX_NOTIFY_PAYLOAD)
            return None
        return payload

    async def run(self) -> None:
        """Serve the channel in both directions until the host cancels this coroutine.

        A process that only publishes still has to run the bridge: the outbound sender lives
        here. Its listener is then harmless, and keeps the process ready to consume topics
        the day it grows a subscriber.
        """
        if self._running:
            message = "topic bridge is already running"
            raise RuntimeError(message)
        self._running = True
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self._listen)
                tasks.start_soon(self._send)
        finally:
            self._listener_ready.clear()
            self._running = False

    def snapshot(self) -> TopicBridgeSnapshot:
        """Return outbound, inbound, and rejection counters for this process."""
        return TopicBridgeSnapshot(
            origin=self.origin,
            channel=self.channel,
            published=self._published,
            local_only=self._local_only,
            notified=self._notified,
            undelivered=self._undelivered,
            received=self._received,
            ignored=self._ignored,
            undecodable=self._undecodable,
        )

    async def _listen(self) -> None:
        while True:
            try:
                async with self.pool.acquire() as connection:
                    disconnected = asyncio.Event()
                    await connection.add_listener(self.channel, self._notified_by)
                    try:
                        # After LISTEN, never before: a resync that ran first would leave
                        # the window it exists to close still open.
                        connection.add_termination_listener(lambda _connection, event=disconnected: event.set())
                        if self.on_resync is not None:
                            await self.on_resync()
                        self._listener_ready.set()
                        await disconnected.wait()
                        self._listener_ready.clear()
                        logger.warning("topic bridge listener on %r disconnected; reconnecting", self.channel)
                    finally:
                        self._listener_ready.clear()
                        # The pool resets a released connection anyway, but asyncpg warns
                        # about the listener it still holds. Shielded so shutdown -- the
                        # usual reason for landing here -- still unsubscribes.
                        with anyio.move_on_after(1.0, shield=True), suppress(Exception):
                            await connection.remove_listener(self.channel, self._notified_by)
            except Exception:
                logger.exception("topic bridge listener on %r failed; reconnecting", self.channel)
            await anyio.sleep(self.reconnect_seconds)

    async def _send(self) -> None:
        while True:
            payload = await self._queue.get()
            self._pending.discard(payload)
            try:
                await self.pool.execute("SELECT pg_notify($1, $2)", self.channel, payload)
            except Exception:
                self._undelivered += 1
                logger.exception("topic bridge could not notify %r", self.channel)
            else:
                self._notified += 1
            finally:
                self._queue.task_done()

    def _notified_by(self, _connection: object, _process_id: int, _channel: str, payload: str) -> None:
        """Republish one remote notification locally, on the event loop thread."""
        origin, separator, remainder = payload.partition(_ORIGIN_SEPARATOR)
        if not separator:
            self._undecodable += 1
            logger.warning("topic bridge received an unattributed payload on %r", self.channel)
            return
        delivery, separator, encoded = remainder.partition(_ORIGIN_SEPARATOR)
        if not separator or delivery not in {_NORMAL_DELIVERY, _TRANSACTION_DELIVERY}:
            self._undecodable += 1
            logger.debug("topic bridge received an unknown delivery mode from %s", origin)
            return
        if origin == self.origin and delivery == _NORMAL_DELIVERY:
            self._ignored += 1
            return
        topic = self.codec.decode(encoded)
        if topic is None:
            self._undecodable += 1
            logger.debug("topic bridge could not decode %r from %s", encoded, origin)
            return
        self._received += 1
        self.bus.publish(topic)
