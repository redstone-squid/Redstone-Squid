# squid-storage

Portable backing stores: versioned scoped byte slots, durable session records, persistent
reactive state, and a Postgres topic bridge. `squid-ui-discord[durable]` uses these contracts
for durable panels; use them directly for your own persistence.

```python
import squid_storage as ss
```

## Scoped stores

A scoped store persists versioned byte slots under `(scope, key)`.

::: squid_storage.ScopedStore

::: squid_storage.MemoryScopedStore

::: squid_storage.SQLiteScopedStore

::: squid_storage.PostgresScopedStore

::: squid_storage.Slot

::: squid_storage.SlotCodec

::: squid_storage.JsonSlotCodec

::: squid_storage.json_codec

## Session stores

::: squid_storage.DurableSessionStore

::: squid_storage.MemorySessionStore

::: squid_storage.SQLiteSessionStore

::: squid_storage.PostgresSessionStore

::: squid_storage.SessionRecord

::: squid_storage.AdmissionToken

::: squid_storage.ClaimToken

## Persistent reactive state

::: squid_storage.PersistentStatePool

## Topic bridging

::: squid_storage.PostgresTopicBridge

::: squid_storage.TopicBridgeSnapshot

## Errors

::: squid_storage.StorageError

::: squid_storage.SlotVersionError
