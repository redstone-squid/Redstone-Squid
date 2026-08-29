"""Snapshot the supported root namespace."""

import squid_storage


def test_public_api_snapshot() -> None:
    expected = {
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
    }
    assert set(squid_storage.__all__) == expected
    assert all(hasattr(squid_storage, name) for name in expected)
