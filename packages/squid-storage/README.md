# squid-storage

Portable versioned key/value storage for Squid applications. The package defines scoped stores,
expiry, schema version checks, codecs, migrations, and a persistent reactive-state pool without
depending on a UI or transport.

This is an alpha release. The Python API may change before 1.0.

```console
pip install squid-storage==0.1.0a1
pip install 'squid-storage[postgres]==0.1.0a1'
```

```python
from squid_storage import MemoryScopedStore, Slot, json_codec

store = MemoryScopedStore()
slot = Slot[str, dict[str, str]]("preferences", json_codec(), version=1)

await store.put(slot, "user:42", {"theme": "dark"})
preferences = await store.get(slot, "user:42")
```

`MemoryScopedStore` is suitable for tests and process-local state. `PostgresScopedStore` provides
the same contract on asyncpg, including versions, expiry, touch, purge, and deletion. The host owns
database pooling and task lifetime.

Run the shared backend contracts with `uv run --locked pytest packages/squid-storage/tests`.
Add `--storage-postgres` to include PostgreSQL through Docker, or supply
`--storage-postgres-dsn` (or `SQUID_STORAGE_TEST_DSN`) for an existing database. Each PostgreSQL
test creates and removes an isolated schema; the test role needs permission to create schemas.

- [Suite overview](https://redstone-squid.github.io/Redstone-Squid/squid-ui/)
- [API map](https://redstone-squid.github.io/Redstone-Squid/squid-ui-api/)
