"""Persist UI sessions and application-scoped values.

DurableSessionStore keeps published sessions and coordinates which host may
update or replace them. ScopedStore holds typed values under a slot name and
scope, such as preferences for one member. PersistentStatePool connects scoped
storage to reactive Reactive, restoring values on load and queuing snapshots
after commits. Memory, SQLite, and PostgreSQL implementations share these
contracts; PostgresTopicBridge carries change notifications between processes.
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
