# 63 — Scoped expiring slots (`packages/squid-slots`)

## Problem

The CascadeUI comparison's fourth finding: Cascade persists named application slots independently
of any UI session, with a per-slot TTL policy. "Persist this guild's dashboard preferences for 30
days" has no answer in this tree.

Squid answers a different and harder question — *reconstruct this exact live UI safely after a
restart* — and answers it well: versioned component recipes, migrations, fenced ownership, whole
component graphs, recovery and checkpointing, SQLite and Postgres
(`packages/squid-layouts/src/squid_layouts/discord/durability/`). None of that is the same problem.
`DurableSessionStore` (`durability/stores.py:53`) is keyed by session record id, its values are a
fixed `(summary_payload, snapshot_payload)` pair, and it has leases rather than expiry.

Nor does the application side have it. The near misses, all checked:

| | durable | TTL | why not this |
|---|---|---|---|
| `TtlCache` (`squid/suggestions/infrastructure/cache.py:17`) | no | yes | in-process, dies with the worker |
| `ArtifactStore` (`squid/artifacts/application.py:16`) | yes | no | bytes by key, nothing expires |
| `IdempotencyService` (`squid/idempotency/application.py:20`) | yes | yes | caller+key to a buffered HTTP response |
| `DurableSessionStore` | yes | lease | session records, not application values |

So a host that wants "this guild's preferences, for a month" writes its own table.

**The consumer is the library user, not this bot.** The bot has a schema-backed, typed
`SettingsStore` (`squid/settings/application/ports.py:9`) and should keep it: real domain data
earns a real schema. This is the standard [27](27-snapshot-stores.md) already set for the
durability backends, and it is the first objection this plan will meet, so it is stated here
rather than defended later.

## Decision

A new dependency-free workspace package, **`packages/squid-slots`**, depending on neither
`squid-layouts` nor `squid-reactive`.

The dependency direction is the whole point. `docs/squid-layouts-architecture.md:268` draws the
line — *"Nothing durable belongs here; anything the application would still want with nobody
looking at it is a service"* — and a slot store is exactly such a service. Putting it inside
`squid_layouts` would make the UI library the durable domain layer, which is the one thing
[90](90-deferred.md)'s Redux rejection exists to prevent. Putting it *below* the UI library, with
no edge pointing up, preserves the separation while still shipping the batteries.

### A slot is declared, not stringly-typed

This is where it beats `store["theme"]`. One declaration fixes the name, the value type, the
expiry policy, and the version:

```python
theme = Slot("theme", codec=json_codec(Theme), ttl=timedelta(days=30))

await slots.get(theme, GuildScope(guild_id))     # -> Theme | None
await slots.set(theme, GuildScope(guild_id), value)
```

`Slot` is the same instinct as [56](56-one-declaration.md): the author states the fact once, in the
place the type checker can see it, instead of restating it at every call site.

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

class SlotStore(Protocol):
    async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None: ...
    async def set[S: Hashable, V](self, slot: Slot[S, V], scope: S, value: V, *, ttl: timedelta | None = None) -> None: ...
    async def delete[S: Hashable, V](self, slot: Slot[S, V], scope: S) -> bool: ...
    async def purge_expired(self) -> int: ...
```

`version` reaches `decode`, so a slot whose shape changes migrates in the codec rather than
needing a framework — the same call [27](27-snapshot-stores.md) made for the snapshot table.
A payload whose version is *newer* than the slot's declaration is refused rather than decoded,
matching `ComponentRegistry._migrate`'s treatment of future snapshots.

The scope is any hashable the host encodes; the `sl.discord.scopes` vocabulary
([59](59-shared-pool.md)) is the conventional one, and encoding it is the host's business, so
`squid-slots` stays Discord-free the same way `squid-reactive` does.

### Three backends, on the conventions already established

`MemorySlotStore`, `SQLiteSlotStore` (stdlib `sqlite3` through `asyncio.to_thread`, zero
dependencies, the library default) and `PostgresSlotStore` (asyncpg behind a `postgres` extra,
import-guarded so the core never imports it). Schema versioning stays dumb — in-band
`schema_version`, migrate-on-open, no framework, because *"the table is a key-value store and
should stay boring"* (`27-snapshot-stores.md:23-25`). Table names go through the same identifier
guard as `durability/stores.py:677-681`, since they are f-string-interpolated into SQL.

Three decisions carry the expiry semantics, and each is a lesson already paid for in this tree:

1. **Expiry is enforced on read *and* by `purge_expired`.** `DurableSessionStore` delegates expiry
   to `DurableSessionRuntime._maintain` (`durability/runtime.py:578-580`), which is right when a
   runtime owns the records. A slot store has no runtime, so a read past the deadline must return
   `None` even when nothing has swept. `purge_expired` is a reclamation sweep, never the thing
   correctness depends on.
2. **Postgres computes deadlines in the database, from a duration.** Lifted from
   `durability/postgres.py:58-70`, whose docstring records why: hosts supply durations, never
   absolute timestamps, so process clock skew cannot decide when something expires.
   `SQLiteSlotStore` cannot do this and inherits `stores.py:258-269`'s shared-clock caveat
   verbatim — every process on one file must agree on the time.
3. **TTL is fixed from write, not sliding.** `touch=True` on `get` opts into extension. A sliding
   default would make "30 days" mean something the author did not write, and the surprise only
   shows up as data that never expires.

## The bridge — where "turnkey" actually is

A key-value table with an expiry column is not more turnkey than what a host would write in an
afternoon. The turnkey part is hydrating a namespace on open and persisting it on commit, and that
is the half worth designing carefully.

`PersistedPool` ships in `squid-slots` behind a `reactive` extra depending on `squid-reactive`.
Direction again: slots may know about reactive state; the UI library must not know about
persistence.

```python
pool = PersistedPool(Preferences, bus, slots=store, slot=preferences_slot)
prefs = await pool.load(GuildScope(guild_id))     # async: it does I/O, and says so
```

Two properties to state plainly rather than bury:

- **`load` is async; `SharedPool.get` stays synchronous.** [59](59-shared-pool.md) fixed
  synchronous factories on the grounds that creating reactive view state performs no I/O.
  Hydration *is* I/O, so this is a different class rather than a widened one — `SharedPool` is not
  touched. It is awaited from `on_load` ([09](09-async-data-loading.md)), which exists for exactly
  this, and a namespace that has not been loaded holds its declared defaults, which is a correct
  state rather than an error.
- **Write-back joins the action; persistence is best-effort.** Registering through
  `squid_reactive.on_action_commit` means a rolled-back action persists nothing, and a
  `SharedStateConflictError` persists nothing. That is the headline: Cascade cannot offer it,
  because its store is not inside the transaction that wrote the state. But `on_action_commit` is
  synchronous, so the write itself is handed to a supervised background drain, and **an action's
  success never depends on the store being reachable**. For preferences that is the right trade;
  the doc must name where it stops being right — anything the application would still want with
  nobody looking at it is a service, and a service is awaited.

A persist failure is reported through the store's error hook, the way `LocalTopicBus` isolates
subscriber failures, and never through the action that triggered it.

## Naming

`squid-slots`, not `squid-store`. The tree already has `ArtifactStore`, `SettingsStore`,
`DurableSessionStore` and now `SlotStore`; a package called "store" would be the fourth
unqualified one and the least specific. "Slot" also names the unit the API is built on, which is
what makes the package's job legible from its name.

## Not included

- No reducers, dispatch, middleware, or subscriptions. This is storage, not a state manager —
  [90](90-deferred.md)'s Redux rejection is untouched and this plan does not lean on it.
- No cross-process invalidation. A slot changing in another process reaches a live UI through
  `Topic` and `PostgresTopicBridge` ([45](45-topic-bridge.md)), which already exist and are
  payload-free by design.
- No query surface beyond exact scope+slot lookup: no listing, no prefix scan, no secondary index.
  Those are the affordances that turn a preferences store into an accidental database.
- No `SharedPool` change, no `Shared` persistence, and no relaxation of `Shared`'s refusal of
  `persist=True`. Persistence lives beside a namespace, never inside one.
- No Alembic migrations. The tables self-create, matching the durability tables, because the bot
  is not a consumer.

## Verification

- The shared `SlotStore` contract runs unconditionally against `MemorySlotStore` and
  `SQLiteSlotStore` (a real temp file); Postgres is integration-gated like the rest of the asyncpg
  surface.
- Round-trip through each backend; a missing scope reads `None`, distinct from a stored `None`.
- A value past its deadline reads `None` with no sweep having run, and `purge_expired` then
  reclaims it and reports the count.
- `touch=True` extends; the default does not. A per-call `ttl=` overrides the slot's.
- `set` twice on one scope replaces rather than accumulating.
- A payload written at `version=1` decodes through a `version=2` slot's codec; a payload from a
  *newer* version is refused rather than decoded.
- Postgres deadlines survive a host clock moved forwards and backwards between `set` and `get`.
- Table-name validation rejects the injection shapes `durability/stores.py` already covers.
- `PersistedPool.load` hydrates from the store and returns a namespace holding declared defaults
  on a miss; two loads of one scope return the identical handle.
- A committed action persists; a rolled-back action and a `SharedStateConflictError` persist
  nothing; a store raising on write fails neither the action nor the flush, and reports once.
- Typing fixtures pin `Slot[GuildScope, Theme]` to `get(...) -> Theme | None`, and reject a
  `UserScope` passed to a guild-scoped slot.
- Focused package tests with `--no-cov`, then `just typecheck`, `alembic heads`, and
  `git diff --check`. `packages/squid-slots` is picked up by the existing `packages/*` workspace
  glob and needs one line in `[tool.uv.sources]`.

## Status

Designed. Independent of [59](59-shared-pool.md) except for the optional `PersistedPool`, which
needs `SharedPool` to exist first.
