"""Backing stores and their backend discipline for Squid applications."""

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
    json_codec,
)
from squid_storage.stores import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySessionStore,
    SQLiteSessionStore,
    SessionRecord,
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
    "Slot",
    "SlotCodec",
    "SlotVersionError",
    "SessionRecord",
    "TopicBridgeSnapshot",
    "json_codec",
]
