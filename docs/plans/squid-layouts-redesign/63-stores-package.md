# 63 — `packages/squid-stores`: extract the backing stores, add a scoped expiring one

## Problem

Two problems that turn out to be one.

**First**, the CascadeUI comparison's fourth finding: Cascade persists named application slots
independently of any UI session, with a per-slot TTL. "Persist this guild's dashboard preferences
for 30 days" has no answer in this tree. Squid answers a different and harder question —
*reconstruct this exact live UI safely after a restart* — and answers it well. The near misses,
all checked:

| | durable | expiry | why not this |
|---|---|---|---|
| `TtlCache` (`squid/suggestions/infrastructure/cache.py:17`) | no | TTL | in-process, dies with the worker |
| `ArtifactStore` (`squid/artifacts/application.py:16`) | yes | none | bytes by key, nothing expires |
| `IdempotencyService` (`squid/idempotency/application.py:20`) | yes | TTL | caller+key to a buffered HTTP response |
| `DurableSessionStore` (`durability/stores.py:53`) | yes | lease | session records, not application values |

**Second**, and this is what makes the first one cheap: `squid_layouts.discord.durability` is
already two packages wearing one name.

| module | lines | imports from `squid_layouts` |
|---|---|---|
| `stores.py` | 724 | **none** — stdlib only |
| `postgres.py` | 690 | **none** — `anyio` plus `runtime.topics`, itself now a re-export of `squid_reactive.topics` |
| `__init__.py` (codecs, `ComponentRegistry`) | 611 | `Mount`, `Component`, targets, presentation |
| `runtime.py` (`DurableSessionRuntime`) | 785 | `Mount`, sessions, delivery |
| `session_records.py`, `frontend.py`, `bot.py` | 555 | `Mount`, sessions, `discord.py` |

1,414 lines — the `DurableSessionStore` protocol, three backends, claim fencing, admission
reservations, table-identifier validation, in-band schema versioning, the clock-skew discipline,
and `PostgresTopicBridge` — sit inside a UI package while depending on nothing in it. The bot's
*only* two imports from `durability` are `PostgresTopicBridge` (`squid/bot/app.py:57`,
`squid/topics.py:17`), which is in that half.

So the storage discipline this tree has already paid for is filed where a non-UI consumer cannot
reach it, and a new store built anywhere else would re-derive it by copy-paste.

## Decision

Extract the storage half into **`packages/squid-stores`**, and add the scoped expiring store there
as one more member rather than as a package of its own.

```
squid-reactive    state kernel, Shared, topics, resources        (no dependencies)
squid-stores      backing stores and their backend discipline    (squid-reactive, anyio)
squid-layouts     the UI                                         (squid-reactive, squid-stores)
```

`squid-stores` depends on `squid-reactive` — `postgres.py` needs `Topic`/`TopicCodec` for the
bridge, and the `PersistedPool` below needs the transaction seam. It does **not** depend on
`squid-layouts`, and nothing in it may. That direction is the whole point:
`docs/squid-layouts-architecture.md:268` draws the line — *"Nothing durable belongs here; anything
the application would still want with nobody looking at it is a service"* — and this package is
where such a service's storage lives. A store inside `squid_layouts` would make the UI library the
durable domain layer, which is what [90](90-deferred.md)'s Redux rejection exists to prevent.

Two units, landable separately.

### Unit 1 — the extraction

`stores.py` and `postgres.py` move verbatim. The import audit above says this is mechanical: no
call site inside them reaches into `squid_layouts`, and `runtime.topics` is already just
`squid_reactive.topics`.

What stays in `squid_layouts.discord.durability` is everything that knows what a `Mount` is: the
snapshot codecs, `ComponentRegistry` and its recipe migrations, `DurableSessionRuntime`,
`session_records.py`, `frontend.py`, `bot.py`. The `LeaseSnapshotStore`/`DurableSessionStore`
boundary does not move — [27](27-snapshot-stores.md) filled it deliberately and this plan does not
re-open it, it relocates the fill.

- `durability/__init__.py` re-exports every moved name, so the package's public surface and the
  bot's two imports are unchanged. Retargeting them at `squid_stores` is a follow-up, not a
  prerequisite, and belongs with [58](58-public-api-narrowing.md)'s tiering rather than here.
- `squid-layouts` gains `squid-stores` as a base dependency. Its `postgres` extra forwards to
  `squid-stores[postgres]` so `squid-layouts[postgres]` keeps meaning what it means today.
- `anyio` becomes a base dependency of `squid-stores`: `postgres.py` imports it unconditionally
  for the bridge's task group and reconnect loop (`postgres.py:607, 649, 653`), unlike `asyncpg`,
  which is import-guarded. Today it rides in on `squid-layouts[discord]`, which would be the wrong
  extra to require for a storage package.
- The store half of `packages/squid-layouts/tests/test_durability.py` moves with the code; the
  recipe/runtime/recovery tests stay.

### Unit 2 — `ScopedStore`

A store keyed by an application scope, holding declared values that expire.

```python
theme = Slot("theme", codec=json_codec(Theme), ttl=timedelta(days=30))

await store.get(theme, GuildScope(guild_id))     # -> Theme | None
await store.put(theme, GuildScope(guild_id), value)
```

**A slot is declared, not stringly-typed.** This is what it has over `store["theme"]`: one
declaration fixes the name, the value type, the expiry policy and the version, in the place the
type checker can see them. Same instinct as [56](56-one-declaration.md) — state the fact once
rather than restating it at every call site.

```python
@dataclass(frozen=True, slots=True)
class Slot[ScopeT: Hashable, ValueT]:
    name: str
    codec: SlotCodec[ValueT]
    ttl: timedelta | None = None
    version: int = 1

class SlotCodec[ValueT](Protocol):
    def encode(self, value: ValueT) -> str: ...
    def decode(self, payload: str, version: int) -> ValueT: ...

class ScopedStore(Protocol):
    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None: ...
    async def put[S: Hashable, V](self, slot: Slot[S, V], scope: S, value: V, *, ttl: timedelta | None = None) -> None: ...
    async def drop[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool: ...
    async def purge_expired(self) -> int: ...
```

`Slot` names the unit, not the package — the objection that killed `squid-slots` as a package name
does not apply to the noun for "one named place a value goes", and no better word survives contact
with this tree (`Record` collides with the bot's domain and `StoredSessionRecord`, `Cell` with
`squid_reactive`'s `_Cell`/`CellAddress`, `Entry` with `GuardLedger._entries`).

`version` reaches `decode`, so a slot whose shape changes migrates in its codec rather than needing
a framework — the same call [27](27-snapshot-stores.md) made for the snapshot table. A payload
whose version is *newer* than the declaration is refused rather than decoded, matching
`ComponentRegistry._migrate`'s treatment of future snapshots.

The scope is any hashable the host encodes. The `sl.discord.scopes` vocabulary
([59](59-shared-pool.md)) is the conventional one, and encoding it is the host's business, so
`squid-stores` stays Discord-free exactly as `squid-reactive` does.

### What co-location earns

`ScopedStore`'s backends are `MemoryScopedStore`, `SQLiteScopedStore` and `PostgresScopedStore`,
and they are built on the extracted machinery rather than beside it:

- the table-identifier guard (`stores.py:677-681`), because table names are f-string-interpolated
  into SQL;
- in-band `schema_version` with migrate-on-open and no framework, because *"the table is a
  key-value store and should stay boring"* (`27-snapshot-stores.md:23-25`);
- `sqlite3` through `asyncio.to_thread` with WAL, and the shared-clock caveat at
  `stores.py:258-269` stated once for both stores;
- asyncpg behind the `postgres` extra, import-guarded so the core never imports it.

Had this shipped as its own package, all five would have been re-derived from a doc citation. That
is the argument for the extraction and the store landing together.

### Expiry

Three decisions, each a lesson already paid for here:

1. **Enforced on read *and* by `purge_expired`.** `DurableSessionStore` delegates expiry to
   `DurableSessionRuntime._maintain` (`durability/runtime.py:578-580`), which is right when a
   runtime owns the records. `ScopedStore` has no runtime, so a read past the deadline returns
   `None` even when nothing has swept. `purge_expired` reclaims space; it is never what
   correctness rests on.
2. **Postgres computes deadlines in the database, from a duration.** Lifted from
   `postgres.py:58-70`, whose docstring records why: hosts supply lease durations, never absolute
   timestamps, so process clock skew cannot decide when something expires. SQLite cannot do this
   and inherits the shared-clock caveat.
3. **TTL is fixed from write, not sliding.** `touch=True` on `get` opts into extension. A sliding
   default would make "30 days" mean something the author did not write, and the surprise shows up
   only as data that never expires.

## The bridge

A keyed table with an expiry column is not more turnkey than what a host writes in an afternoon.
The turnkey part is hydrating a namespace on open and persisting it on commit.

`PersistedPool` ships in `squid-stores`, which is why the package depends on `squid-reactive`
rather than taking it as an extra:

```python
pool = PersistedPool(Preferences, bus, store=store, slot=preferences_slot)
prefs = await pool.load(GuildScope(guild_id))     # async: it does I/O, and says so
```

- **`load` is async; `SharedPool.get` stays synchronous.** [59](59-shared-pool.md) fixed
  synchronous factories because creating reactive view state performs no I/O. Hydration *is* I/O,
  so this is a different class, not a widened one — `SharedPool` is untouched. It is awaited from
  `on_load` ([09](09-async-data-loading.md)), which exists for this, and an unloaded namespace
  holding its declared defaults is a correct state rather than an error.
- **Write-back joins the action; persistence is best-effort.** Registering through
  `squid_reactive.on_action_commit` means a rolled-back action persists nothing, and a
  `SharedStateConflictError` persists nothing. That is the headline, and Cascade cannot offer it,
  because its store is not inside the transaction that wrote the state. But `on_action_commit` is
  synchronous, so the write goes to a supervised background drain and **an action's success never
  depends on the store being reachable**. For preferences that is the right trade; the boundary
  where it stops being right is the architecture doc's own line — anything the application would
  still want with nobody looking at it is a service, and a service is awaited.

A persist failure reports through the store's error hook, the way `LocalTopicBus` isolates
subscriber failures, and never through the action that triggered it.

## Naming

`squid-stores`, plural: the package is the stores, and the plural keeps it distinct from the
`*Store` classes it defines. `squid-durability` was rejected because a 30-day preferences slot is
not durability in the session-recovery sense, and the word is already load-bearing for the half
that stays behind. `squid-slots` was rejected as naming one member for the whole. Nautical options
(`squid-hold`) collide with "hold" as lock acquisition, which this package is full of.

## Not included

- **No `LeaseSnapshotStore` / `DurableSessionStore` boundary change.** The protocols move house;
  their shape is [27](27-snapshot-stores.md)'s and stays.
- **No `discord/` split.** Whether `squid-layouts` should mean *layout* — with the ~35-module
  Discord adapter as its own package — is the larger question this extraction invites and does not
  answer. It wants its own plan and its own evidence.
- No reducers, dispatch, middleware, or subscriptions. This is storage, not a state manager;
  [90](90-deferred.md)'s Redux rejection is untouched and this plan does not lean on it.
- No cross-process invalidation *of slots*. A value changing in another process reaches a live UI
  through `Topic` and the bridge that now lives in this package
  ([45](45-topic-bridge.md)) — payload-free by design.
- No query surface beyond exact scope+slot lookup: no listing, no prefix scan, no secondary index.
  Those are the affordances that turn a preferences store into an accidental database.
- No `Shared` persistence and no relaxation of its refusal of `persist=True`. Persistence lives
  beside a namespace, never inside one.
- No Alembic migrations. Tables self-create, matching the durability tables today, because the bot
  is not a consumer of either store.

## Consumers

None in the bot for `ScopedStore`, deliberately — it has a schema-backed typed `SettingsStore`
(`squid/settings/application/ports.py:9`) and should keep it: real domain data earns a real schema.
The consumer is the library user, which is the standard [27](27-snapshot-stores.md) already set.
The bot's contribution is the test suite.

The extraction, by contrast, has an immediate consumer: the bot's two `PostgresTopicBridge`
imports stop reaching through a UI package for a `LISTEN`/`NOTIFY` client.

## Verification

**Unit 1.** The moved modules keep their existing tests, run from the new package. `import
squid_stores` in a fresh environment without `squid-layouts` installed. Every name previously
exported from `squid_layouts.discord.durability` still imports from there —
`test_public_api.py`'s exact-set assertions are the enforcement. `squid-layouts[postgres]` still
installs asyncpg. The bot boots and `open_topic_bridge` still returns a live bridge.

**Unit 2.** The shared `ScopedStore` contract runs unconditionally against the memory and SQLite
backends (a real temp file); Postgres is integration-gated like the rest of the asyncpg surface.

- Round-trip per backend; a missing scope reads `None`, distinct from a stored `None`.
- A value past its deadline reads `None` with no sweep having run; `purge_expired` then reclaims it
  and reports the count.
- `touch=True` extends, the default does not; a per-call `ttl=` overrides the slot's.
- `put` twice on one scope replaces rather than accumulating.
- A `version=1` payload decodes through a `version=2` codec; a payload from a *newer* version is
  refused rather than decoded.
- Postgres deadlines survive a host clock moved forwards and backwards between `put` and `get`.
- Table-name validation rejects the injection shapes `stores.py` already covers — asserted against
  the shared guard, not a copy.
- `PersistedPool.load` hydrates, returns declared defaults on a miss, and two loads of one scope
  return the identical handle.
- A committed action persists; a rolled-back action and a `SharedStateConflictError` persist
  nothing; a store raising on write fails neither the action nor the flush, and reports once.
- Typing fixtures pin `Slot[GuildScope, Theme]` to `get(...) -> Theme | None` and reject a
  `UserScope` passed to a guild-scoped slot.

Then focused tests with `--no-cov`, `just typecheck`, `alembic heads`, and `git diff --check`.
`packages/squid-stores` is picked up by the existing `packages/*` workspace glob and needs one line
in `[tool.uv.sources]`.

## Status

Designed. Unit 1 is independent and mechanical. Unit 2's `PersistedPool` needs
[59](59-shared-pool.md)'s `SharedPool` to exist first; `ScopedStore` itself does not.
