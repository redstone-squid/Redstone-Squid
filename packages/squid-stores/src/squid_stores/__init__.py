"""Backing stores and their backend discipline for Squid applications."""

from squid_stores.persisted import PersistedPool
from squid_stores.postgres import PostgresSnapshotStore, PostgresTopicBridge, TopicBridgeSnapshot
from squid_stores.scoped import (
    JsonSlotCodec,
    MemoryScopedStore,
    PostgresScopedStore,
    ScopedStore,
    Slot,
    SlotCodec,
    SlotVersionError,
    SQLiteScopedStore,
    json_codec,
)
from squid_stores.stores import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySnapshotStore,
    SQLiteSnapshotStore,
    StoredSessionRecord,
)

__all__ = [
    "AdmissionToken",
    "ClaimToken",
    "DurableSessionStore",
    "JsonSlotCodec",
    "MemoryScopedStore",
    "MemorySnapshotStore",
    "PersistedPool",
    "PostgresScopedStore",
    "PostgresSnapshotStore",
    "PostgresTopicBridge",
    "SQLiteScopedStore",
    "SQLiteSnapshotStore",
    "ScopedStore",
    "Slot",
    "SlotCodec",
    "SlotVersionError",
    "StoredSessionRecord",
    "TopicBridgeSnapshot",
    "json_codec",
]
