# 40 — Shared store: view state that outlives one mount

## Problem

Three primitives sit next to a hole none of them covers.

- **`sl.state()`** is transactional: a failed action restores what it found, and
  [28](28-history.md) can reverse a committed one. It is also strictly per-component and
  strictly per-mount. A sibling panel cannot read it, and it dies with the message.
- **`TopicBus`** ([26](26-topic-bus.md)) crosses mounts, but it is payload-free *on
  purpose* — coalescing is only sound when a dropped duplicate carries nothing. It says
  "re-read the world", and it has no opinion about what happens when the action that
  published fails afterwards.
- **A shared service** carries values across mounts today, and that is the right answer
  whenever the value is domain truth. It is the wrong answer for view state, because it
  sits outside `transaction()`: a handler that writes the service and then raises leaves
  the write standing, and `sl.history()` cannot reverse it without a hand-written inverse
  for something the framework should have owned.

The gap, stated once:

> **UI-owned state whose lifetime exceeds one mount, whose writes still belong to the
> action that made them.**

Two live panels agreeing on a filter, a preference the user set in a settings panel that
a browse panel should honour immediately, a selection shared between a list and the
detail view beside it. Every one of these is view state — nothing outside the screen
wants it — and today each costs an application-side service plus a hand-written undo
inverse plus a bus topic, three mechanisms deep for one value.

### What this overturns, and what it does not

[90](90-deferred.md) rejects a store, and reaffirmed it on 2026-08-22 when the bus moved
package-side. That rejection is overturned here **only for the narrow case above**, on
the productization standard that already carried [26](26-topic-bus.md) and
[27](27-snapshot-stores.md): the consumer is the library user, and hand-rolling
transactional cross-mount view state is exactly the "getting three things right at once"
problem 26 refused to leave to the reader.

The rejected parts of 90's store stay rejected, and the design is shaped to make them
unreachable rather than merely discouraged:

| 90 rejected | Status here |
|---|---|
| dispatch → middleware → reducers → subscribers | Rejected. Reads and writes are direct, in `sl.state()`'s shape. There is no dispatch and no interception point. |
| A global singleton store | Rejected. Stores are constructed and owned by the host, like `Reactor` and the session registry. |
| A second source of domain truth | Rejected. `Controlled`/`Managed` ([10](10-selection-ownership.md)) still governs ownership, and §3's lifetime rules make the store an unsuitable place to keep anything durable. |
| Payloads on the bus | Rejected, and untouched. The bus still carries addresses; the store publishes cell addresses through it and subscribers re-read the store. |

## Design

> The store holds what the view owns. Anything the application would still want if no one
> were looking at it belongs to the application's data layer.

### 1. Public surface

```python
THEME = sl.atom("theme", Theme.SYSTEM)
FILTER = sl.atom("build-filter", factory=BuildFilter)
SELECTED = sl.atom("selected-build", None)

store = sl.SharedStore(bus)                       # the host's TopicBus, per §7
preferences = store.bind(sl.Scope("prefs", user.id, guild.id))

class Toolbar(sl.Component):
    def __init__(self, preferences: sl.SharedState) -> None:
        self.preferences = preferences

    def render(self) -> sl.Node:
        theme = self.preferences.watch(THEME)
        ...

    async def choose(self, event: sl.ActionEvent, build_id: int) -> None:
        self.preferences.set(SELECTED, build_id)
        self.history.record(f"Select build {build_id}")
```

| Operation | Semantics |
|---|---|
| `get(atom)` | Read. Inside an action, this transaction's staged value if it has one, else the latest committed value. |
| `watch(atom)` | `get()`, plus a render dependency (§7). Outside a render it *is* `get()` — no error, no dependency. |
| `set(atom, value)` | Blind write. Staged inside an action, committed immediately outside one. Last commit wins. |
| `reset(atom)` | `set()` to the atom's declared default. |
| `update(atom, fn)` | Read-modify-write against the transaction-visible value. Derived from committed state, it records that cell's revision as a commit precondition. |
| `expect(atom, expected)` | Guard without writing. Compares now, and records the observed revision as a commit precondition. |
| `topic(atom)` | The bus address of this cell, for a host that wants to follow it by hand (§7). |

`expect(...)` followed by `set(...)` is compare-and-set; there is no `compare_and_set`,
for the reason in *Rejected alternatives*.

**Naming.** `Scope` is the address; `SharedState` is the bound handle. Name bindings for
their role — `preferences`, `workspace` — not their mechanics.

### 2. Atoms and scopes

**Atom identity is object identity.** The string is diagnostic: it appears in conflict
messages, history labels and devtools, and nowhere in the keying. Two atoms both named
`"theme"` are two cells under one scope. This is `ContextKey`'s existing model
(`@dataclass(frozen=True, slots=True, eq=False)`), and it should be spelled the same way.

**Defaults are values.** No `has()`, no `watch_optional()`, no `Unset`. If absence means
something, it is a value in `T` — `None`, or a member of the enum. A store that cannot
report absence cannot grow a presence protocol later by accident.

**`Scope` is a frozen, hashable address** and nothing else: no fallback, no inheritance,
no lifetime, no persistence. Same atom under two scopes is two cells, and one action may
write both.

### 3. Lifetime: the store is not immortal

A bot process runs for weeks across thousands of guilds. `Scope("prefs", user.id,
guild.id)` keyed into a plain dict is an unbounded leak, and it is the failure this
design is most likely to ship with, because it never shows up in a test that finishes.

1. **`SharedStore.bind(scope)` returns a handle and registers interest.** Handles are
   cheap and comparable; the store counts distinct live handles per scope with weak
   references.
2. **A scope with no live handles and no live subscribers is dropped**, cells and all, at
   the next commit that touches the store. Reads through a stale handle after that see
   defaults again, which is the correct answer for view state: nobody was looking.
3. **`store.discard(scope)`** drops it now, for a host that knows the session is over —
   the session registry's `on_finish` is the obvious caller.
4. **`store.scopes()`** returns live scopes for devtools ([25](25-devtools-cog.md)) and
   for the leak test.

Weak *subscriber* references are not enough here. The cells are the memory.

### 4. Values are immutable on the way out

Local state proxies its containers: `self.items.append(x)` invalidates, because
`ReactiveList` intercepts it. A store that copies on read would make
`shared.get(ITEMS).append(x)` a silent no-op — the same code, the same shape, no error,
no effect. That is the worst available outcome.

**Reads return immutable values.** `list` → `tuple`, `dict` → a frozen mapping, `set` →
`frozenset`, recursively; anything else is returned as-is on the assumption that the
author put a value in. The mistake raises `AttributeError` at the call site instead of
vanishing. Writes accept ordinary mutable values and freeze on the way in, so authoring
is unaffected: `shared.update(ITEMS, lambda items: [*items, x])`.

**Equality short-circuit.** `set()` to an equal value is a no-op: no revision bump, no
publish, no history change. "Equal" means `is`, then `==` inside a `try` that treats a
raising or non-boolean comparison as *not equal* — the same conservative shape
`_Computed.refresh_for` already uses, and stricter than `_State.__set__`, which
short-circuits on `is` alone. Frozen values make this cheap.

### 5. Actions: staging, one commit

Writes inside an action stage into a per-store overlay and become visible together, or
not at all. Outside an action they commit synchronously.

Guarantees, in the vocabulary the reader already has:

- **No dirty reads.** Another action never sees staged values.
- **Read-your-writes.** An action sees its own.
- **Read committed.** Two `get()` calls either side of an `await` may differ. Documented,
  not defended against; snapshot isolation is not on offer.
- **Atomic publication.** One action's shared writes appear together, with its local
  state writes, at one commit.

#### 5a. The transaction participant seam — and a bug it fixes

`transaction()` today runs `commit()` in the `else:` branch, *after* `_CURRENT.reset(token)`
and outside the `try`:

```python
else:
    _CURRENT.reset(token)
    current.commit()
```

So anything raising inside `commit()` — a commit hook, and `History._push` invalidates
its owner from one — propagates with **no rollback**, leaving the action's writes standing
and no owner notified. Nothing exercises that path today, which is why it is still there.

Prepare-can-fail makes it reachable, so the first deliverable is closing it, and it is
worth landing on its own: restructure `transaction()` so the commit sequence is inside
the failure path, and rollback runs if any part of it raises.

The sequence, with participants:

```text
ACTION
  local sl.state writes           -> the transaction's snapshots, unchanged
  shared writes                   -> a per-store overlay, registered as a participant

COMMIT
  1. PREPARE each participant      validate revision guards and reservations; freeze
                                   the prepared writes
  2. any failure                   rollback local state, discard every overlay,
                                   raise SharedConflict
  3. APPLY each participant        synchronous, no awaits, no failure paths left;
                                   bump revisions
  4. FINALIZE                      build the delta, run commit hooks, notify owners,
                                   publish cell topics
```

Step 3 is the whole reason prepare exists: everything that can fail happens before
anything is visible.

#### 5b. Conflict rules

| Operation | Precondition it records |
|---|---|
| `set` / `reset` | None. Blind. |
| `update` from committed state | That cell's observed revision. |
| `expect` | That cell's observed revision. Revision rather than value, so an A→B→A round trip is still a conflict. |
| `get` / `watch` | None. |

Once a transaction has a guard on a cell, **it keeps the original revision for the rest of
the transaction.** A later blind `set()` to the same cell does not clear it. The
conservative direction is the correct one: the action already branched on what it read.

### 6. What a conflict looks like

`SharedConflict` names the store, scope and atom, with the atom's diagnostic string.

**There is no automatic retry.** Re-running a handler that may have already sent a
message or written a row is not safe, and the framework cannot tell which handlers those
are.

A conflict therefore travels the ordinary failed-handler path: the transaction rolls
back, nothing is published, and the mount's error handling shows what it shows for any
raising handler. An author who wants better says so:

```python
try:
    self.workspace.update(SELECTION, add(build_id))
except sl.SharedConflict:
    await event.notice(self.chrome.changed_elsewhere)
```

`Chrome` gains `changed_elsewhere` for this and for §8's stale control, because the
package cannot contain `_()` markers.

### 7. Reactivity through the bus

The package has exactly one cross-mount refresh mechanism and should keep having one. The
store does not grow a subscriber index; it **publishes cell addresses on the `TopicBus`
it was constructed with.**

1. `SharedState.topic(atom)` is the address vocabulary — one constructor, per 26's rule,
   used by both the automatic path and a host that wants `reactor.follow(mount,
   prefs.topic(THEME))` by hand.
2. During render, `watch()` records `(store, scope, atom)` into a render-observation
   collector — the ContextVar shape `provide()`/`inject()` already uses.
3. The rendered result carries the deduplicated address set, and the mount reconciles its
   follows against it through `Reactor.follow`.
4. Commit step 4 publishes the addresses whose cells actually changed. Coalescing,
   at-most-one-drain-per-topic and the delivery contract are the bus's, already tested.

**Reconcile at stage time, not after delivery.** `Reactor.follow`'s own docstring gives
the reason: a write landing between a mount's read and its subscription is lost, and the
bus is not durable, so that panel is stale until someone clicks it. A staged render that
is later discarded leaves a subscription the next successful render removes, and the
worst case is one spurious refresh. Over-subscribe; never under-subscribe.

The store's construction argument is a real bus, not an optional one. A `bus=None`
default would mean two notification paths and a store that silently stops being reactive.
Tests construct a `TopicBus` and call `drain()`, which is what that seam is for.

### 8. History

Shared cells are framework-owned state, so one `HistoryEntry` covers an action's local
writes and its shared writes across every scope it touched. The author does not owe an
inverse for state the framework can restore just because it crossed a mount boundary.

```python
async def select(self, event: sl.ActionEvent, build_id: int) -> None:
    self.open = True
    self.workspace.set(SELECTED, build_id)
    self.history.record(f"Select build {build_id}")
```

Everything hard here comes from one fact: **an undo stack is per-component, and a shared
cell is not.** Two panels can hold entries over one cell, and the second undo must not
resurrect a value the first user's later change replaced.

1. **Every cell carries a monotonic revision.** A shared change records the revision it
   wrote.
2. **A direction is applicable only if every cell it would restore still sits at the
   revision that entry wrote.** Otherwise the whole operation fails with
   `HistoryConflict` (a `HistoryError`) and changes nothing — no partial restore, in
   either half.
3. **Guards evolve.** Undo writes new revisions, so the entry's redo guard is the set undo
   just produced. `HistoryEntry` and the deltas stay frozen: restoring returns an updated
   delta and `History` replaces the entry on its stack with `dataclasses.replace`.
4. **`can_undo` validates.** It already gates the `history_actions` controls, so a stale
   entry disables its own control at the owning panel's next render, and the common case
   is a greyed-out button rather than an error. The check is a dict lookup per touched
   cell.
5. **A press that still races raises.** Validation cannot be atomic with a click.
   `history_actions` catches `HistoryConflict` and shows `chrome.changed_elsewhere`
   privately; `History.undo()` called directly raises, as it must.

#### 8a. The external-inverse race

`History._reverse` runs the author's world inverse *first*, deliberately: a failed inverse
must leave the reader's view alone. That ordering means a preflight check is not enough —
the inverse awaits, another action can commit a shared write while it does, and the
postflight would then have to fail *after* the world was already reversed. Partial world,
un-restored state, exactly what the ordering exists to prevent.

For the narrow intersection of **entries that have both shared changes and an external
inverse**, the store reserves those cells for the duration of the operation. A competing
commit fails its prepare with `SharedConflict` rather than waiting. Reservations are
internal: no public lock, no acquisition API, no waiting anywhere.

Phase 4 opens with a test that reproduces the race against an unreserved implementation.
If it cannot be reproduced, the reservation does not ship.

### 9. Composition

Unchanged from what the package already does, and deliberately so.

- Constructor injection at ownership boundaries is the default.
- `ContextKey` + `provide()`/`inject()` is the prop-drilling escape hatch. It is a
  dependency mechanism and a `SharedState` handle is a dependency; a second context
  system would be the actual mistake.
- Nearest-provider shadowing already gives a preview or sandbox subtree its own binding.
- Render-time `inject()` semantics do not change here.

```python
PREFERENCES = sl.ContextKey[sl.SharedState]("preferences")

class Dashboard(sl.Component):
    def render(self) -> sl.Node:
        self.provide(PREFERENCES, self.preferences)
        return self.embed(Content(), key="content")
```

## Phases

| # | Deliverable | Exit criteria |
|---|---|---|
| 0 | Restructure `transaction()` so a raising commit rolls back; add the participant protocol. | A hook that raises rolls the action back and notifies no owner (a bug fix, testable with no store). Two fake participants commit together; one failing prepare applies neither. |
| 1 | `Atom`, `Scope`, `SharedStore`, `SharedState`; `get`/`set`/`reset`/`update`/`expect`/`topic`; immediate-outside-an-action behaviour; immutable reads; scope lifetime. | Identity, defaults, per-scope independence, equal-value no-op, frozen reads, `discard` and drop-on-last-handle. |
| 2 | Overlays, read-your-writes, revision guards, prepare/apply. | A raising handler leaks no staged value; `update` and `expect` conflicts raise `SharedConflict`; ABA is caught. |
| 3 | `watch()` observation, stage-time follow reconciliation, publication on commit. | Two mounts react to one commit, once each; a dropped conditional `watch` stops refreshing; no follow outlives its mount. |
| 4 | Shared deltas in history, evolving guards, validating `can_undo`, `HistoryConflict`, the reservation if §8a's test justifies it. | One entry undoes local plus multi-scope shared state; an intervening write disables the control and refuses the press; the inverse race produces no partial world. |
| 5 | Docs, conflict diagnostics, devtools scope/cell/revision inspection, one real consumer. | Examples cover one atom across scopes, deep injection, provider shadowing; the leak test holds under repeated bind/discard. |

## Verification

- `packages/squid-layouts/tests/test_transactions.py`: the phase-0 fix — a raising commit
  hook rolls back and notifies nobody; participant prepare/apply ordering; a failing
  prepare applies nothing.
- `packages/squid-layouts/tests/test_shared_store.py` (new): identity and scope keying;
  defaults and `reset`; frozen reads raising on mutation; the equality no-op; immediate
  writes outside an action; staging, read-your-writes and rollback inside one; `update`
  and `expect` conflicts including A→B→A; guard stickiness after a later blind `set`;
  `discard`, drop-on-last-handle, and a bind/discard loop that ends with no cells.
- `packages/squid-layouts/tests/test_shared_reactivity.py` (new): `watch` outside a render
  is `get`; observation reconciliation across renders; two mounts refreshed once each by
  one commit through a real bus and `drain()`; a discarded staged render leaves no
  permanent follow.
- `packages/squid-layouts/tests/test_history.py`: one entry spanning local and two scopes;
  an intervening write making `can_undo` false and the press raise `HistoryConflict` with
  nothing changed; guard evolution across undo → redo → undo; the §8a race.
- `test_public_api.py`: the new exports, and the no-discord import check extends to the
  store module — it is portable core.
- `just typecheck` (compare against a pre-change run; the tree is not at zero) and
  `git diff --check`.

## Consumers

None on day one, and the plan does not invent one. The library user is the consumer, per
the productization standard — but phase 5 does not close until one real panel pair uses
it, because [26](26-topic-bus.md) and [27](27-snapshot-stores.md) both shipped without an
in-tree consumer and a third would be a pattern rather than a coincidence. The settings
panel's theme/locale reaching a second live panel is the candidate to try first, and if
it turns out that a service plus a topic reads better there, that is a finding worth
having before phases 1–4 generalize.

## Rejected alternatives

- **`compare_and_set`.** Inside a transaction its return value would be a lie: `True`
  means "valid right now, may still raise at commit". CAS means something specific
  everywhere else, and a CAS that can fail after returning `True` is worse than no CAS.
  `expect(...)` then `set(...)` is the same operation, deferred honestly.
- **Designed-for multi-store transactions.** Nothing has produced a case where two stores
  take part in one action. The participant loop is a list, so it falls out and one test
  pins the atomicity property; no cross-store guarantee beyond prepare-all-then-apply-all
  is documented, and none is designed for.
- **A weak subscriber index inside the store.** A second copy of `TopicBus`'s coalescing
  and delivery contract, with a second set of failure modes. The store publishes; the bus
  delivers.
- **Payloads on the bus.** Still rejected, for 26's reasons, in full. Cell addresses are
  addresses.
- **Reference-copy atoms (`copy="ref"`).** `sl.state()` has it for services and guilds
  that cannot be copied. A shared cell holding an uncopyable collaborator is a service,
  and should be injected as one — allowing it would put a live mutable object behind
  revisions that cannot see it change.
- **Snapshot isolation, or a public `atomic()` / lock.** Read-committed plus explicit
  guards keeps every wait visible. A public lock in a handler is a deadlock in a UI.
- **Automatic retry on conflict.** Unsafe for handlers with external effects, and the
  framework cannot identify them.
- **Hierarchical scope fallback** (guild scope backing user scope). Real, and cleanly
  expressible today by reading two bindings and choosing. Encoding precedence in `Scope`
  makes every read a search and every conflict ambiguous about which cell it means.
- **Persistence.** Deferred, and honestly: identity-keyed atoms mean the store is not
  serializable without an atom registry. Say so rather than leaving a door that is
  already nailed shut. If durable view state is ever wanted, that registry is the first
  design question, not an implementation detail.
- **Reducers, middleware, dispatch, a global singleton.** 90's rejection, unchanged.
