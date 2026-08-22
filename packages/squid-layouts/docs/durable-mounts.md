# Durable mounts

Use the SQLite store when one host or a shared filesystem owns the database:

```python
from squid_layouts.discord.durability import (
    ComponentRegistry,
    MountLocator,
    MountManager,
    MountReachability,
    SQLiteSnapshotStore,
)


class DiscordLocatorResolver:
    async def resolve(self, locator: MountLocator) -> MountReachability:
        try:
            await fetch_message(locator.values["channel_id"], locator.values["message_id"])
        except MessageNotFound:
            return MountReachability.MISSING
        except MessageFetchError:
            return MountReachability.UNREACHABLE
        return MountReachability.REACHABLE


registry = ComponentRegistry()
registry.register("review", version=1, factory=ReviewPanel)
store = SQLiteSnapshotStore("mounts.sqlite3", table_name="review_mounts")
manager = MountManager(registry, store, locator_resolver=DiscordLocatorResolver())
```

Call `checkpoint()` at application-owned durability boundaries. At startup, `recover(access=...)` claims
available snapshots and checks each locator before restoring it. A confirmed missing frontend is
deleted; permissions failures and outages retain the snapshot for a later recovery attempt.
Recovery must receive the access policy for the mounts it reconstructs; snapshots do not infer an
owner or silently restore public controls.

For multi-host deployments, install `squid-layouts[postgres]` and pass an `asyncpg.Pool` to
`PostgresSnapshotStore`. Both implementations use the same `LeaseSnapshotStore` contract, and both
accept a custom unqualified `table_name`.
