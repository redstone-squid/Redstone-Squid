"""Backing stores and their backend discipline for Squid applications."""

from squid_storage.persisted import PersistedPool
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
    json_codec,
)
from squid_storage.stores import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySessionStore,
    SQLiteSessionStore,
    StoredSessionRecord,
)

__all__ = [
    "AdmissionToken",
    "ClaimToken",
    "DurableSessionStore",
    "JsonSlotCodec",
    "MemoryScopedStore",
    "MemorySessionStore",
    "PersistedPool",
    "PostgresScopedStore",
    "PostgresSessionStore",
    "PostgresTopicBridge",
    "SQLiteScopedStore",
    "SQLiteSessionStore",
    "ScopedStore",
    "Slot",
    "SlotCodec",
    "SlotVersionError",
    "StoredSessionRecord",
    "TopicBridgeSnapshot",
    "json_codec",
]
