"""Optional asyncpg adapters: the fenced durable-session store and the topic bridge."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import anyio

from squid_reactivity.topics import Address, KindKeyCodec, Topic, TopicBus, TopicCodec
from squid_storage.stores import (
    _SCHEMA_VERSION,
    AdmissionToken,
    ClaimToken,
    SessionRecord,
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


_DEFAULT_TABLE_NAME = "squid_sessions"
_DEFAULT_TOPIC_CHANNEL = "squid_topics"
_ORIGIN_SEPARATOR = ":"
_NORMAL_DELIVERY = "normal"
_TRANSACTION_DELIVERY = "transaction"
_MAX_NOTIFY_PAYLOAD = 8000
"""Exclusive upper bound for the complete encoded NOTIFY payload, in bytes."""


class PostgresSessionStore:
    """Persist fenced durable sessions through an asyncpg pool.

    PostgreSQL is the multi-host store. Every expiry comparison and every new
    deadline uses ``clock_timestamp()`` in the database; hosts supply lease
    durations, never absolute timestamps, so process clock skew cannot create
    overlapping owners. Claim and admission fences come from one PostgreSQL
    sequence and therefore increase monotonically across all callers.

    Args:
        pool: Caller-owned asyncpg connection pool; the store creates its tables lazily.
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

    async def list(self) -> tuple[SessionRecord, ...]:
        await self._initialize()
        rows = await self.pool.fetch(
            f"SELECT key, scope, snapshot_payload, record_payload FROM {self.table_name} ORDER BY key"
        )
        return tuple(_stored_record(row) for row in rows)

    async def load(self, key: str) -> SessionRecord | None:
        _validate_key(key)
        await self._initialize()
        row = await self.pool.fetchrow(
            f"SELECT key, scope, snapshot_payload, record_payload FROM {self.table_name} WHERE key = $1",
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

    async def save(self, token: ClaimToken, snapshot_payload: str, record_payload: str) -> bool:
        await self._initialize()
        saved = await self.pool.fetchval(
            f"""
            UPDATE {self.table_name} SET snapshot_payload = $4, record_payload = $5
            WHERE key = $1 AND claim_owner = $2 AND claim_fence = $3
              AND lease_until > clock_timestamp()
            RETURNING TRUE
            """,
            token.key,
            token.owner,
            token._fence,
            snapshot_payload,
            record_payload,
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

    async def inspect(self, reservation: AdmissionToken) -> tuple[SessionRecord, ...] | None:
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
                SELECT key, scope, snapshot_payload, record_payload
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
        snapshot_payload: str,
        record_payload: str,
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
            # asyncpg builds `PoolConnectionProxy`'s query methods with a metaclass, so nothing
            # static records that the object a pool yields is a `Connection`.
            if valid is not True or not await self._retirement_is_valid(
                connection,  # pyright: ignore[reportArgumentType]  # asyncpg's pool proxy is untyped
                reservation.scope,
                key,
                victims,
            ):
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
                    (key, scope, snapshot_payload, record_payload, claim_owner, claim_fence, lease_until)
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    clock_timestamp() + ($7::double precision * INTERVAL '1 second')
                )
                """,
                key,
                reservation.scope,
                snapshot_payload,
                record_payload,
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
                        snapshot_payload TEXT NOT NULL,
                        record_payload TEXT NOT NULL,
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


def _stored_record(row: Record) -> SessionRecord:
    return SessionRecord(
        key=str(row["key"]),
        scope=str(row["scope"]),
        snapshot_payload=str(row["snapshot_payload"]),
        record_payload=str(row["record_payload"]),
    )


@dataclass(frozen=True, slots=True)
class TopicBridgeSnapshot:
    """Diagnostic counters for this bridge instance.

    Notified counts successful pg_notify calls, including calls in transactions that
    may later roll back; it does not confirm delivery to another process.
    """

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
    """Carry topic addresses between processes over PostgreSQL LISTEN/NOTIFY.

    Hosts publish through the bridge to reach other processes; publishing directly on
    the local bus stays local. Incoming notifications publish on that bus without
    being sent out again, so peers do not echo one another indefinitely.
    Payloads identify topics, not application state; subscribers re-read their source
    of truth. Delivery is best effort: queue overflow, send failure, or disconnected
    listeners can lose notifications, so consumers need another way to resynchronize.

    Args:
        pool: Caller-owned asyncpg pool. The listener reserves one connection while
            connected; sending requires another available connection.
        bus: Local bus receiving this host's publishes and accepted notifications.
        codec: Topic address encoding, defaulting to kind:key. Cell addresses stay local.
        channel: PostgreSQL channel shared by the participating processes.
        on_resync: Awaited after LISTEN succeeds on each connection, before the listener
            is marked ready, to refresh state after potentially missed notifications.
        reconnect_seconds: Delay before rebuilding a lost listener connection.
        queue_size: Maximum outbound notifications queued while the sender is busy or stopped.
        origin: Identity used to suppress echoes of immediate local publishes. Transactional
            publishes keep their own echo so local delivery also waits for commit.
            Defaults to a fresh UUID; use a distinct identity for each bridge.
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
        """Publish locally now and queue encodable topics for best-effort remote delivery.

        Identical payloads coalesce while queued; a full queue drops the remote notification.
        Queued notifications wait for run if the sender is stopped. Sending uses the bridge's
        connection and is not ordered with an uncommitted application write; use publish_in
        when delivery must wait for that transaction.
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
        """Notify through the supplied connection, delivering locally through the listener too.

        Wait for the running listener to be ready, then call pg_notify on the supplied
        connection. With an active transaction, delivery waits for commit and rollback
        suppresses it. Without one, the statement commits immediately. This method neither
        starts nor commits the caller's transaction.

        Raises:
            RuntimeError: If the bridge is not running.
            ValueError: If the topic cannot be encoded within the NOTIFY payload limit.
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
        """Own the listener and sender until the host cancels this coroutine.

        Even a publishing-only host must run the sender. Cancellation does not guarantee
        that queued notifications have been sent and does not close the caller's pool.
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
        """Republish an accepted notification on the local event loop, including transaction echoes."""
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
