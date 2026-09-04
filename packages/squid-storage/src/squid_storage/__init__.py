"""Durable session stores, scoped values, shared-state persistence and the PostgreSQL topic bridge.

Memory and SQLite backends are single-host; the asyncpg backends are the only ones safe across hosts.
"""

from squid_storage.persistent_state import PersistentStatePool
from squid_storage.postgres import PostgresSessionStore, PostgresTopicBridge, TopicBridgeSnapshot
from squid_storage.scoped import (
    JsonSlotCodec,
    MemoryScopedStore,
    PostgresScopedStore,
    ScopedStore,
    Slot,
    SlotCodec,
    SlotVersionError,
    SQLiteScopedStore,
    StorageError,
    json_codec,
)
from squid_storage.stores import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySessionStore,
    SessionRecord,
    SQLiteSessionStore,
)

__all__ = [
    "AdmissionToken",
    "ClaimToken",
    "DurableSessionStore",
    "JsonSlotCodec",
    "MemoryScopedStore",
    "MemorySessionStore",
    "PersistentStatePool",
    "PostgresScopedStore",
    "PostgresSessionStore",
    "PostgresTopicBridge",
    "SQLiteScopedStore",
    "SQLiteSessionStore",
    "ScopedStore",
    "SessionRecord",
    "Slot",
    "SlotCodec",
    "SlotVersionError",
    "StorageError",
    "TopicBridgeSnapshot",
    "json_codec",
]
