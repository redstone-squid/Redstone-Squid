# 27 — Snapshot stores and the reachability sweep

## Problem

90 rejected persistence batteries as "storage backends for an unused subsystem" — correct
by the consumer standard, superseded by the productization standard: a library whose
durability story is "bring your own `LeaseSnapshotStore`" has no durability story. The
boundary itself was judged ready when that entry was written; this plan fills it without
moving it. The comparison's one genuine operational gap also lands here: recovery today
will resurrect a mount whose message was deleted while the process was down.

## Design

> The protocols do not move; two implementations and one sweep arrive behind them.

1. **`durability/stores.py`**, two `LeaseSnapshotStore` implementations:
   - **`SQLiteSnapshotStore(path)`** — stdlib `sqlite3` through `asyncio.to_thread`, zero
     new dependencies. The library default: persistence works with a file path. One
     table (`key, payload, owner, lease_until`), WAL mode, claims via
     `UPDATE … WHERE lease_until < now` so the lease race is the database's problem.
   - **`PostgresSnapshotStore(pool)`** — asyncpg behind an optional extra
     (`squid-layouts[postgres]`), import-guarded so the core package never imports it.
     Same table shape; claims via `UPDATE … RETURNING`.
2. **Schema versioning stays dumb**: a `schema_version` value in-band, migrate-on-open,
   no migration framework. The payloads are already versioned by `SnapshotCodec`; the
   table is a key-value store and should stay boring.
3. **Reachability sweep** in `MountManager.recover`: before restoring a snapshot, verify
   its `MountLocator` still resolves — a 404 on the message deletes the snapshot instead
   of resurrecting a mount pointed at nothing. Fetch failures that are *not* 404
   (permissions, outage) skip restore and keep the snapshot: unreachable is not gone.
4. **Testing**: SQLite against a real temp file in the package suite, including the
   lease-contention case. Postgres tests are integration-gated like the rest of the
   asyncpg surface; the shared store contract runs against `MemorySnapshotStore` and
   SQLite unconditionally.

## Consumers

None in the bot today, deliberately — routed statelessness still covers its real cases,
which is the design working. The consumer is the library user; the bot's contribution is
the test suite and a documented example.
