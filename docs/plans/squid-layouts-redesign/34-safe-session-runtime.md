# 34 — Safe session runtime

## Problem

Plans 12/24 and 27 closed the first version of the three operational gaps found in the
CascadeUI comparison, but they stopped at mechanisms:

- `Mount.lock_to` admits one user or a fixed set, but `None` silently means anyone and a
  mount has no way to ask an asynchronous authorization policy.
- `MountRegistry` safely enforces one live mount per arbitrary key with replace/reject and
  parent cascade, but its shipped `SessionKey` cannot honestly name guild-only or global
  sessions, `open()` conflates rejection with abandoned delivery, and one logical session
  is still represented by one `Mount`.
- `MountManager` can encode, store, lease, restore, and prune snapshots, while SQLite and
  Postgres stores now ship. The host must still reconnect restored mounts to Discord,
  schedule lease renewal, choose every checkpoint, coordinate direct mount finishes with
  record deletion, and keep `MountManager` and `MountRegistry` consistent.

The pieces are individually sound, but the safe path is not the easy path. A user who wants
"one private settings panel per user and guild, surviving restarts" must understand three
independent lifecycle owners and write the joins between them. That remains CascadeUI's real
product advantage: its current instance API includes limits, scopes, protected replacement,
and participants, while `PersistenceMiddleware` owns the complete recovery pipeline. Its
ordinary views are owner-only by default, although persistent views deliberately default to
public. Checked against the current documentation on 2026-08-22:

- <https://hollowthesilver.github.io/CascadeUI/guide/views/>
- <https://hollowthesilver.github.io/CascadeUI/guide/persistence/>

Squid should not copy class-variable policy or make a mount guess who owns it. It should make
access explicit at the low level and provide one high-level session runtime that composes
delivery, cardinality, lifetime, and optional durability.

This plan ignores backwards compatibility. `squid-layouts` is pre-1.0 and has no published
consumer contract worth preserving at the cost of a permanently ambiguous API. Superseded
names are deleted and in-tree consumers migrate in the same series; there are no aliases or
deprecation shims.

## Boundaries

The three questions remain orthogonal even though one runtime coordinates them:

| Question | Owner |
|---|---|
| Who may interact with this message? | `AccessPolicy` on `Mount` |
| How many logical sessions may occupy this scope? | `SessionRegistry` |
| What state and message binding survive process death? | `DurableSessionRuntime` |

`Mount` remains one component tree bound to one Discord message. A `Session` is operational:
it owns one root mount and any attached child mounts. `SessionRegistry` owns only live process
state. `SnapshotStore` remains a narrow storage boundary. `DurableSessionRuntime` is the
optional coordinator over those pieces, not a second renderer or state store.

Application and database state remain authoritative. Durable snapshots contain UI-local
component and presentation state only; this plan does not revive a Redux/global store or add
an untyped `session.data` bag.

## A. Explicit mount access

### 1. `access=` is required

Delete `lock_to`. Every `Mount` declares an access policy:

```python
mount = sl.discord.Mount(panel, access=sl.discord.Owner(interaction.user.id))
board = sl.discord.Mount(role_board, access=sl.discord.Everyone())
game = sl.discord.Mount(game, access=sl.discord.Users({host_id, opponent_id}))
```

There is no default. `Mount` cannot infer an owner from a component or destination, and a
fail-open default is unsafe while a fail-closed owner default is impossible without an actor.
Requiring `Everyone()` is intentional friction: public interaction is a security decision,
not the absence of one. Static documents that need no live interaction use `render_static`.

`AccessPolicy` is asynchronous so a host can check roles, permissions, leases, or an
application service without bypassing the mount funnel:

```python
class AccessPolicy(Protocol):
    async def check(self, interaction: discord.Interaction) -> AccessDecision: ...


@dataclass(frozen=True, slots=True)
class Allowed:
    pass


@dataclass(frozen=True, slots=True)
class Denied:
    reason: TextLike | None = None


type AccessDecision = Allowed | Denied
```

The built-ins are `Everyone`, `Owner`, `Users`, and `Check`. They accept integer IDs; Discord
model coercion is convenience with little value in a typed API. `Check` wraps an async
callable and is the extension point, so the framework does not grow role- or bot-owner-specific
policy classes.

`Mount._begin_dispatch` awaits the policy before renewing activity or clearing status. A
`Denied(reason=None)` uses `Chrome.not_yours`; an explicit reason is localized and returned
ephemerally. Policy exceptions enter the normal mount error funnel and never admit the click.
Modal submissions pass through the same check, as they do for `lock_to` today.

Routed controls keep their middleware authorization. They have no mount and therefore no
meaningful `AccessPolicy`; the two APIs share decision vocabulary only if doing so makes the
implementation simpler.

### 2. Safe convenience paths

Two helpers distinguish click ownership from message visibility:

```python
mount = sl.discord.owned_mount(panel, interaction.user.id, timeout=900)

opened = await sl.discord.open_personal(
    sessions,
    panel,
    interaction,
    key=SessionKey.user_guild("settings", interaction.user.id, interaction.guild_id),
)
```

`owned_mount` only constructs `Mount(..., access=Owner(...))`. It does not imply ephemeral
delivery. `open_personal` owns the complete common Discord path: owner access, ephemeral
`respond_to`, session registration, and replacement policy. Naming the first helper
`private_mount` would be dishonest because a locked channel message is still publicly visible.

The generic `SessionRegistry.open` continues to accept an already constructed mount and a
`Destination`, so prefix-command visibility, DM fallback, files, and host-specific audience
rules stay expressible.

## B. Sessions and cardinality

### 1. Session identity

Replace the current mandatory-user `SessionKey` shape with a stable name plus an arbitrary
hashable scope and shipped constructors:

```python
SessionKey.user("account", user_id)
SessionKey.guild("roles", guild_id)
SessionKey.user_guild("settings", user_id, guild_id)
SessionKey.global_("status")
SessionKey.custom("build-edit", (user_id, build_id))
```

The constructors produce typed, frozen scope values rather than magic strings. Registry APIs
still accept any `Hashable`; `SessionKey` is the conventional spelling and the shape the
durability codec knows how to serialize.

### 2. A session is not a mount

Rename `MountRegistry` to `SessionRegistry` and let it store `Session` records:

```python
class Session:
    key: Hashable | None
    root: Mount
    mounts: tuple[Mount, ...]
    participants: frozenset[int]

    async def attach(self, mount: Mount, destination: Destination, *, actor_id: int | None = None) -> OpenResult: ...
    async def join(self, user_id: int) -> JoinResult: ...
    async def leave(self, user_id: int) -> None: ...
    async def finish(self, *, disable: bool = True) -> None: ...
```

The root plus attached children replace the registry's private parent-to-entry graph. Finishing
the session finishes every mount depth-first and tolerates an unreachable sibling. A child may
itself own descendants. `Mount.on_finish` still observes independent terminal paths; if the root
finishes directly, the session closes and cascades. A child finishing directly only detaches
that branch.

Participants are operational identities, not component state. `join` and `leave` maintain the
reverse index used for cardinality and replacement protection. They do not silently rewrite a
mount's `AccessPolicy`: an open lobby may allow everyone to press Join while its game controls
use a policy that consults `session.participants` explicitly.

### 3. Structured open outcomes

Change `Mount.send` and session opening so abandoned delivery is data rather than an ambiguous
`None`:

```python
type SendResult = Delivered | Abandoned
type OpenResult = Opened | Rejected | Abandoned

@dataclass(frozen=True, slots=True)
class Opened:
    session: Session

@dataclass(frozen=True, slots=True)
class Rejected:
    occupants: tuple[Session, ...]
    reason: RejectionReason
```

`Delivered` carries the `DeliveryReceipt`. Destination failures still raise and leave the
mount re-sendable. The registry no longer wraps a destination to discover whether it ran.
Callers can distinguish collision, participant capacity, protected replacement, and abandoned
delivery without a racy `get()` pre-check. Host call sites still own user-facing rejection
wording.

### 4. Cardinality and collision policy

Replace `WhenOpen` with data policy:

```python
SessionPolicy(
    limit=1,
    collision=ReplaceOldest(),
    protect=ProtectCrossUserAttachments(),
    participant_limit=None,
)
```

`limit=None` is unlimited and a positive integer caps the sessions under one key. The built-in
collision policies are `Reject` and `ReplaceOldest`; a `CollisionPolicy` protocol receives the
opening request and current occupants and returns either rejection or the exact victims. This
is the extension point for application-specific cost, priority, or persistent-panel rules.

Protection is an independently composable predicate over each proposed victim. The default
`ProtectCrossUserAttachments` rejects replacement when a victim has participants other than
the opener or a child attributed to another actor. `Unprotected` is explicit. Persistent
sessions additionally reject replacement by a non-durable request unless the caller explicitly
chooses a policy that retires the durable record.

The per-key lock remains held across admission, delivery, registration, and victim finish:

1. Read current occupants and select victims.
2. Reject without delivering if policy or protection refuses.
3. Deliver the newcomer.
4. Register it only when delivery committed.
5. Finish victims only after successful delivery.

This preserves plan 12's strongest guarantee: a Discord failure cannot cost the user the
incumbent and the replacement. Cleanup remains identity-checked because victim finish hooks run
after the newcomer is registered.

Participant registration is atomic across every scope index it occupies. If joining would
exceed `participant_limit` or make the participant violate another session limit, no index is
changed and `JoinResult` explains the refusal. The first implementation ships with a lobby/game
example and race tests; without that worked consumer, participant indexing is omitted rather
than guessed at from Cascade's API.

## C. Batteries-included durability

### 1. Replace dual managers with one coordinator

Retain `ComponentRegistry`, codecs, and the store protocols, but replace public `MountManager`
with `DurableSessionRuntime`. It composes a `SessionRegistry`, a component registry, a leased
store, and a frontend adapter:

```python
runtime = sl.discord.durability.DurableSessionRuntime(
    sessions=sessions,
    components=components,
    store=SQLiteSnapshotStore("mounts.sqlite3"),
    frontend=DiscordFrontend(bot),
)

report = await runtime.recover()

async with anyio.create_task_group() as tasks:
    tasks.start_soon(runtime.run)
    await bot.start(token)
```

The runtime never starts its own task. `run()` owns lease renewal, coalesced checkpoint retries,
and expiry pruning under the host's anyio task group. The low-level stores remain usable without
it.

Opening a durable session is one operation. It claims or reserves the durable key before
delivery, opens through `SessionRegistry`, derives the Discord locator from the committed
delivery receipt, and writes the first snapshot. Abandonment or failure releases the reservation.
Ephemeral or otherwise temporary edit authority is rejected before a durable record is accepted:
an ephemeral message has no recoverable permanent identity.

### 2. Checkpoint at visible commit boundaries

Add an asynchronous mount-presented observer fired after a candidate has been successfully
delivered and committed. `DurableSessionRuntime` registers one observer for every mount in a
durable session and checkpoints the whole session's UI-local state after visible commits. State
mutations and failed renders never reach storage merely because they happened in memory.

Discord and the database cannot share a transaction. The contract is therefore explicit:

- Discord delivery commits first; the snapshot never claims a generation the reader did not see.
- A failed checkpoint leaves the live mount usable, marks the durable session unhealthy, and
  enters a bounded retry queue owned by `run()`.
- A process crash between the Discord commit and snapshot save may restore the previous visible
  generation. Applications whose domain mutation matters independently already persist that
  mutation in their authoritative service; the snapshot is not its transaction log.

Normal `Session.finish()` deletes the durable record and releases its claim after mount teardown.
Replacement transfers ownership through the registry/runtime operation rather than relying on
two unrelated finish calls. Shutdown releases leases without deleting records.

### 3. Frontend recovery is supplied

Replace the reachability-only resolver with a frontend protocol that returns an actionable
binding:

```python
class DurableFrontend(Protocol):
    async def resolve(self, locator: MountLocator) -> Reachable | Missing | Unreachable: ...
    async def reconnect(self, mount: Mount, binding: Reachable) -> None: ...
```

`DiscordFrontend` ships in the Discord extra. It fetches the channel and message, distinguishes
definitive 404 from temporary inability, edits a reachable message with the restored mount's new
render and control IDs, retains a permanent edit handle, and only then registers the session as
live. Recovery does not depend on stale process-local mount IDs or generations.

Confirmed missing messages delete their records. Unreachable messages retain and release their
records for a later pass. Reconnection failures are isolated per record, not raised out of the
whole sweep.

`recover()` returns a structured `RecoveryReport` with at least:

- `restored`
- `missing`
- `expired`
- `unreachable`
- `incompatible`
- `failed`
- `claimed_elsewhere`

Each item carries the durable key, locator when decodable, and a sanitized reason. One malformed
payload or moved component cannot prevent unrelated panels from returning.

### 4. Migration and fencing

`ComponentRegistry.register` accepts a sequential snapshot migration chain. A migration receives
the decoded raw snapshot for version N and must return version N+1; the registry validates and
re-encodes after every step. Factories still construct known types—there are no dynamic imports.
Missing migrations put the record in `incompatible` and retain it for operator action.

Leases become fenced ownership, not advisory timestamps:

- `claim` returns an opaque, monotonically changing claim token.
- `save`, `renew`, and `delete` for an owned record compare that token atomically.
- Newly opened durable sessions claim before their first record is published, just like recovered
  sessions.
- Postgres lease expiry uses database time so host clock skew cannot create two owners. SQLite
  remains a single-host/shared-filesystem option and documents its clock assumption.
- Losing a claim finishes and unregisters the local session before another checkpoint can write.

The shared store contract tests cover stale-writer rejection. Postgres is not described as
multi-host safe until those tests pass against a real database.

## D. In-tree migration and documentation

There is no compatibility phase:

1. Replace every `Mount(..., lock_to=...)` with `Owner`, `Users`, `Check`, or explicit
   `Everyone`; audit rather than mechanically translating existing `None` values.
2. Replace `MountRegistry`, `WhenOpen`, and the re-export shim with `SessionRegistry`,
   `SessionPolicy`, and structured results. Migrate the settings, poll wizard, build editor,
   and consent consumers.
3. Convert personal interaction-only call sites to `open_personal` where it genuinely owns
   both access and audience. Prefix/DM-aware host destinations stay explicit.
4. Replace `MountManager` tests and documentation with `DurableSessionRuntime`; codecs and store
   contract tests remain low-level.
5. Add a decision guide: routed controls for long-lived authoritative posts, durable sessions for
   stateful UI drafts that must survive restarts, and ordinary sessions for transient panels.
6. Remove plan 90's participant deferral only when the lobby/game example and its participant
   race tests land. Until then the policy protocol ships without speculative participant indexes.

## Implementation sequence

Each commit is independently valid and reviewable:

1. `discord: require explicit mount access` — policy vocabulary, denial funnel, mount and consumer
   migration, then delete `lock_to`.
2. `sessions: return explicit delivery outcomes` — change `Mount.send`, add `OpenResult`, migrate
   callers away from `None` tests.
3. `sessions: make scope and cardinality first-class` — new keys, `Session`, `SessionRegistry`,
   limits and collision/protection protocols; delete the old registry names.
4. `durability: fence snapshot ownership` — claim tokens and shared store contract, then SQLite and
   Postgres implementations.
5. `durability: reconnect Discord sessions` — frontend protocol, Discord adapter, per-record report,
   and migration chain.
6. `durability: supervise session checkpoints` — presented observer, retry queue, lease/expiry run
   loop, finish/delete integration, and end-to-end documentation.
7. `sessions: track participants` — only with the worked multi-user example required above.

## Verification

- Access tests cover every built-in policy, asynchronous denial, localized reasons, policy errors,
  modal submission, and the fact that a denied click does not renew idle lifetime.
- A construction/type-check fixture proves `Mount(component)` without `access=` is invalid; every
  in-tree mount is audited as owned, allowlisted, checked, or public.
- Session tests retain plan 12's failed/abandoned replacement, identity cleanup, cascade, and racing
  opens, then add all scopes, structured results, limits greater than one, protected replacement,
  and participant join races when that phase lands.
- Durability contract tests run against memory and a real SQLite file; integration-gated Postgres
  tests prove fencing, lease loss, schema initialization, and concurrent claims.
- Recovery tests prove one corrupt/incompatible/unreachable record does not block a healthy one,
  missing and expired records are pruned, temporary failures are retained, migrations are sequential,
  and stale writers cannot checkpoint after takeover.
- A fake Discord frontend proves recovery edits the existing message with the restored mount's new
  control IDs before admitting interaction. A live experiment covers a real restart, click, message
  deletion, and recovery report.
- Run the focused package suites with `--no-cov`, `just typecheck`, relevant changed-file formatting
  and linting, `git diff --check`, and the existing architecture tests. Defer the full suite to CI
  unless the mount funnel changes reveal a broader blast radius.

## Status

Proposed 2026-08-22.
