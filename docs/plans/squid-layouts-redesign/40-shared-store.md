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

A shared namespace is a class, declared the way a component declares its own state.

```python
@dataclass(frozen=True, slots=True)
class Member:
    user_id: int
    guild_id: int

class Preferences(sl.Shared[Member]):
    theme: Theme = sl.cell(Theme.SYSTEM)
    locale: Locale = sl.cell(Locale.EN)

class Workspace(sl.Shared[Member]):
    selected: int | None = sl.cell(None)
    filters: tuple[str, ...] = sl.cell(())

store = sl.SharedStore(bus)                       # the host's TopicBus, per §7
preferences = Preferences(store, Member(user.id, guild.id))
workspace = Workspace(store, Member(user.id, guild.id))

class Toolbar(sl.Component):
    def __init__(self, preferences: Preferences, workspace: Workspace) -> None:
        self.preferences = preferences
        self.workspace = workspace

    def render(self) -> sl.Node:
        theme = self.preferences.theme
        ...

    async def choose(self, event: sl.ActionEvent, build_id: int) -> None:
        self.workspace.selected = build_id
        self.history.record(f"Select build {build_id}")
```

`sl.cell(default)` is `sl.state(default)` one level out: the same declared-type-is-the-real-type
signature (`cell(default: ValueT) -> ValueT`), the same `factory=` alternative, the same
descriptor mechanics, reading through a store instead of an instance `__dict__`.

| Expression | Semantics |
|---|---|
| `handle.cell` | Read. Inside an action: this transaction's staged value if it has one, else the latest committed value, and the revision read is remembered (§5b). Inside a render: also records a render dependency (§7). Outside both: a plain read. |
| `handle.cell = value` | Write. Staged inside an action, committed immediately outside one. |
| `del handle.cell` | Reset to the declared default. A write, and never a read, so never guarded. |
| `handle.topics.cell` | The cell's bus address, for a host that wants to follow it by hand (§7). |
| `handle.scope` | The address this handle is bound to, typed (§2). |

That is the whole surface. There is no `get`, `set`, `watch`, `update`, `expect` or
`compare_and_set`; *Rejected alternatives* records why each is absent.

**The handle is typed by what it holds, and by where it holds it.** `Preferences` is a real type, so a component
that wants preferences asks for `Preferences`, `sl.ContextKey[Preferences]` says which
binding it means, and reading a workspace cell off a preferences handle is an attribute
error before it is anything else. An untyped handle carrying free-standing atom keys
would have made that a silent read of a default. `sl.Shared[Member]` types the address
the same way, so `handle.scope.guild_id` is an `int` and the two ids cannot be passed in
the wrong order.

### 2. Cells and scopes

**Cell identity is descriptor identity.** `Preferences.theme` and `Workspace.theme` are
two cells because they are two descriptors, and no rule has to say so — it falls out of
the declaration the way `ContextKey`'s `eq=False` identity does, without the class having
to be written that way on purpose.

**The diagnostic name comes free.** `__set_name__` gives the attribute name and the
owning class, so conflicts, history labels and devtools say `Preferences.theme` with
nothing declared twice and nothing that can drift from the variable it is assigned to.

**Defaults are values.** No `has()`, no `Unset`, no absence protocol. If absence means
something it is a value in the declared type — `None`, or a member of the enum. A store
that cannot report absence cannot grow a presence protocol later by accident.

**A cell is keyed `(descriptor, scope)`.** Descriptors are unique to their class, so two
namespaces at one address cannot collide and no namespace discriminator is needed inside
the address. One class bound to two scopes is two independent sets of cells, and one
action may write both.

**The scope is a value the application declares**, and `Shared` is generic in it. It must
be frozen and hashable and mean nothing else: no fallback, no inheritance, no lifetime, no
persistence. A frozen dataclass is the intended shape, and it is an idiom the author
already has — nothing about it is the store's invention.

```python
@dataclass(frozen=True, slots=True)
class Member:
    user_id: int
    guild_id: int

class Preferences(sl.Shared[Member]): ...
```

Declaring the type is worth the four lines. It makes the address ordered by name rather
than by position, it makes `handle.scope` readable and typed, and it makes co-scoping —
two namespaces deliberately sharing one address, and so one `discard` (§3) — something
you opt into by naming the type rather than something two equal integer tuples can do by
accident. `sl.Shared` unparameterised means `sl.Shared[sl.Scope]`, and `sl.Scope(*parts)`
remains for a namespace small enough not to want a declared address; §3 says what that
costs.

An unhashable scope, or a dataclass that is not frozen, raises when the handle is
constructed. That is a store-correctness failure rather than a matter of style — cells
keyed by a value that can change are cells nothing can reach again.

**A few attribute names are reserved** — `topics`, and the handle's own `store` and
`scope`. Declaring a cell with one of those names raises at class creation (§4).

### 3. Lifetime: the store is not immortal

A bot process runs for weeks across thousands of guilds. `Member(user.id, guild.id)`
keyed into a plain dict is an unbounded leak, and it is the failure this design is most
likely to ship with, because it never shows up in a test that finishes.

**A namespace's cells at an address live exactly as long as some handle to that pair.**
Constructing `Preferences(store, address)` registers a weak reference with the store; the
weakref callback of the last live handle drops that namespace's cells at that address.
Handles are the only input to the decision, because anything that watches a cell reached
it through a handle it still holds — a mount following `preferences.topics.theme` holds
the component that holds the handle.

**The bucket is `(class, scope)`, not the scope alone.** It is what a weakref to a handle
gives you, since a handle knows its own class; bucketing per scope would mean deliberately
discarding that. It is also the better answer: `Preferences` and `Workspace` at one
address drop independently, each when its own last handle dies, instead of a live
`Workspace` keeping `Preferences` cells alive for as long as it lasts.

Dropping on the weakref callback rather than sweeping at the next commit is deliberate: a
store nobody is writing to gets no commits, and that is exactly the idle store that is
leaking.

- **`store.discard(scope)`** drops every namespace at that address now, for a host that
  knows the session is over. The session registry's `on_finish` is the obvious caller,
  and it sweeps across classes because a host that knows a session ended usually does not
  know which panels bound namespaces to it.
- **`store.bindings()`** returns the live `(class, scope)` pairs, for devtools
  ([25](25-devtools-cog.md)) and for the leak test.

**`discard` is the reason to declare a scope type.** Its sweep is by address value, so two
namespaces at equal addresses are discarded together — intended when they co-scope on
purpose, and a hazard with bare `sl.Scope(5)`, where an unrelated namespace that also
reached for `sl.Scope(5)` is swept with it. `Member(...)` and `Session(...)` are never
equal no matter what integers they hold.

**The hazard this creates, stated out loud.** A dropped scope reads back as defaults, and
because §2 has no absence API that is indistinguishable from a scope nobody ever wrote.
For view state the answer is right — nobody was looking — but it means a premature drop
is silent, looks like normal operation, and will be reported as "it forgot my filter"
rather than as a bug in lifetime. `store.bindings()` and the devtools view exist partly
so that this is observable at all.

### 4. Values: a cell holds what you do not mutate in place

**The rule is one line: assign to change a cell.** Mutating a value read out of a cell is
not observed. This is the bargain React makes with `setState`, and it is the same bargain
here.

The store does not enforce it, and three ways it could were considered and dropped.

**Not by freezing reads.** Coercing `list` → `tuple` and `dict` → a frozen mapping on the
way out makes the declared type a lie — there is no way to spell "frozen `T`", so
`cell([])` would be declared `list[str]` and hand back a tuple. It is also only ever
partial: a dataclass cell whose field is a list sails straight through, so the guarantee
would hold for the case an author is least likely to get wrong and fail for the one they
are most likely to get wrong. A half-guarantee teaches the wrong reflex.

**Not by validating every write.** An immutability predicate at write time costs a pass
over the value on the hot path to reject values the declaration already described, and
adds an error path to learn for a mistake the type system can express.

**Not by proxying**, the way `ReactiveList` does for local state. A proxy would have to
route each in-place mutation into the transaction as a write, which puts partial
mutations behind revision guards that cannot see them.

**The declaration does the work instead.** Declare cells with immutable types and the
common mistake is a hard error at no cost, because the type has no mutating method:

```python
class Workspace(sl.Shared[Member]):
    filters: tuple[str, ...] = sl.cell(())

workspace.filters.append(tag)          # AttributeError, at typecheck and at runtime
workspace.filters = (*workspace.filters, tag)
```

**One honest difference from React.** There, mutating state is a private no-op: your
component does not re-render and nothing else is affected. Here the value read out of a
cell *is* the committed value other mounts read, so mutating it in place is not a no-op
but an unobserved cross-mount change — no revision bump, no publish, surfacing whenever
some unrelated thing makes another panel render. The penalty for the same mistake is
worse, which is the real reason to declare cells immutable rather than a reason for the
store to police it.

**So the declaration is checked once, at class creation.** `__set_name__` sees the
annotation; a cell declared `list`, `dict` or `set` raises at import:

```text
Preferences.tags is declared list[str]. A shared cell holds a value that is not
mutated in place -- declare tuple[str, ...].
```

Once per class, nothing on the hot path, no coercion and no per-write validation.
`_State.__init__` already raises at declaration time for an incoherent `copy="ref"` plus
`persist`, so the seam and the precedent both exist. An author who genuinely wants a
mutable cell declares a type that is not one of the three and owns the consequence.

**Equality short-circuit.** Assigning an equal value is a no-op: no revision bump, no
publish, no history change. "Equal" means `is`, then `==` inside a `try` that treats a
raising or non-boolean comparison as *not* equal — the conservative shape
`_Computed.refresh_for` already uses, and stricter than `_State.__set__`, which
short-circuits on `is` alone. Values that are not mutated in place make this cheap and
make it mean what it says.

### 5. Actions: staging, one commit

Writes inside an action stage into a per-store overlay and become visible together, or
not at all. Outside an action they commit synchronously.

Guarantees, in the vocabulary the reader already has:

- **No dirty reads.** Another action never sees staged values.
- **Read-your-writes.** An action sees its own.
- **Read committed.** Two reads either side of an `await` may differ. Documented, not
  defended against; snapshot isolation is not on offer.
- **Atomic publication.** One action's shared writes appear together, with its local state
  writes, at one commit.

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
  shared writes                   -> a per-store overlay, enlisted with join_action()

COMMIT  -- fallible half, nothing visible yet
  1. PREPARE each participant      validate revision guards and reservations; freeze
                                   the prepared writes
  2. BUILD the state delta         the deep copy a recorder will need
     any failure above             abort every participant, restore local state,
                                   raise (SharedConflict, for a store)
COMMIT  -- published; the action has happened
  3. APPLY each participant        synchronous, no awaits, no failure paths left
  4. NOTIFY owners                 _state_changed, as today
  5. FINALIZE each participant     publish cell topics, wake watchers
  6. RUN commit hooks              recorders, i.e. sl.history
```

Two orderings in there are load-bearing.

**Everything fallible happens before step 3.** That is the whole reason prepare exists,
and it is what lets `transaction()` roll an action back after its handler returned
cleanly.

**Commit hooks run last, after publication.** A recorder's effect leaves the transaction
— `sl.history` pushes an entry onto a stack that outlives it — so a hook must not run
anywhere a later failure could still roll the action back, or the stack ends up holding
an entry for an action that never happened. Putting them last also fixes the real damage
in today's bug: a raising hook now leaves the action committed *and its owners notified*,
so the panel re-renders correctly and only the recording is missing. Before, the writes
landed and nothing was told, which is a silently stale screen.

The participant seam itself is public: `sl.ActionParticipant` and
`sl.join_action(key, factory)`, alongside the `sl.on_action_commit` it sits next to. A
library user with their own transactional subsystem gets the same commit as the store.

#### 5b. Conflicts: one rule, derived from what the action did

> **A cell that a transaction both reads and writes carries the revision it read as a
> commit precondition. Everything else is blind.**

```python
async def toggle(self, event: sl.ActionEvent) -> None:
    self.preferences.theme = flip(self.preferences.theme)   # read + write -> guarded
    self.preferences.locale = Locale.EN                     # write only   -> blind
```

That is compare-and-set, `update()` and `expect()` all three, derived from what the code
did rather than declared alongside it. `workspace.filters = (*workspace.filters, tag)` is
a guarded read-modify-write with no ceremony, and the author cannot forget to ask for the
guard, because asking for it is not a separate act.

**Read *and* write, not read alone.** A handler that reads a shared cell to compose a
message and never writes it records nothing; guarding on reads alone would make every
read-only action conflict-prone, which is a false positive with no lost update behind it.
The overlay keeps the revision each cell was read at and prepare validates only the
intersection of the read set and the write set.

**Revision, not value**, so an A→B→A round trip is still a conflict.

**The guard is the first revision read**, and a later write does not clear it: the action
already branched on what it read.

**Outside an action there are no guards.** There is no transaction to hold a read
revision, so every immediate write is blind and last commit wins.

What this costs: an action cannot deliberately force a blind overwrite of a cell it read
earlier in the same handler. The failure is a retryable conflict rather than corruption,
and the workaround is to write before reading. That is the whole downside, and it buys
the removal of three operations.

### 6. What a conflict looks like

`SharedConflict` names the store, scope and cell, using the cell's `Owner.attribute`
diagnostic name.

**A handler cannot catch it.** `transaction()` wraps the handler at `mount.py:1509`, so
prepare runs when the `with` block exits — after the handler body has returned. A
`try/except sl.SharedConflict` around a write catches nothing, and the doc must not
suggest one.

A conflict therefore travels the ordinary failed-handler path: the transaction rolls back,
nothing is published, and the mount's `handle_error` shows what it shows for any raising
handler. `Chrome` gains `changed_elsewhere` so the default message says something true
about the cause, here and for §8's stale control.

An application that wants more than that has the seam it already has for
application-wide action policy — `sl.ActionMiddleware`, which wraps the transaction
rather than sitting inside it:

```python
class ExplainConflicts(sl.ActionMiddleware):
    async def dispatch(self, request: sl.ActionRequest, proceed: sl.ActionProceed) -> None:
        try:
            await proceed()
        except sl.SharedConflict:
            await request.event.notice(chrome.changed_elsewhere)
```

**There is no automatic retry.** Re-running a handler that may have already sent a message
or written a row is not safe, and the framework cannot tell which handlers those are.

### 7. Reactivity through the bus

The package has exactly one cross-mount refresh mechanism and should keep having one. The
store does not grow a subscriber index; it **publishes cell addresses on the `TopicBus`
it was constructed with.**

1. `handle.topics.cell` is the address vocabulary — one constructor, per 26's rule, used
   by both the automatic path and a host that wants `reactor.follow(mount,
   preferences.topics.theme)` by hand. Every topic is a `Topic`, so the attribute proxy
   loses no type information; an unknown name raises listing the class's cells.
   `topics.py` declares `type Topic = Hashable`, so the address *is* the `(descriptor,
   scope)` pair — both halves are hashable by §2's rules. Nothing has to be encoded into
   a string, which means no canonical form to get wrong and no collision surface invented
   on the way to the bus.
2. During render, a cell read records `(store, scope, descriptor)` into a
   render-observation collector — the ContextVar shape `provide()`/`inject()` already
   uses. This is why there is no separate `watch()`: the context the read happens in is
   what distinguishes a render dependency from a transaction read, and both are just
   reading the attribute.
3. The rendered result carries the deduplicated address set, and the mount reconciles its
   follows against it through `Reactor.follow`.
4. Commit step 5 publishes the addresses whose cells actually changed. Coalescing,
   at-most-one-drain-per-topic and the delivery contract are the bus's, already tested.

**Reconcile at stage time, not after delivery.** `Reactor.follow`'s own docstring gives
the reason: a write landing between a mount's read and its subscription is lost, and the
bus is not durable, so that panel is stale until someone clicks it. A staged render that
is later discarded leaves a subscription the next successful render removes, and the worst
case is one spurious refresh. Over-subscribe; never under-subscribe.

The store's construction argument is a real bus, not an optional one. A `bus=None` default
would mean two notification paths and a store that silently stops being reactive. Tests
construct a `TopicBus` and call `drain()`, which is what that seam is for.

### 8. History

Shared cells are framework-owned state, so one `HistoryEntry` covers an action's local
writes and its shared writes across every scope it touched. The author does not owe an
inverse for state the framework can restore just because it crossed a mount boundary.

```python
async def select(self, event: sl.ActionEvent, build_id: int) -> None:
    self.open = True
    self.workspace.selected = build_id
    self.history.record(f"Select build {build_id}")
```

Everything hard here comes from one fact: **an undo stack is per-component, and a shared
cell is not.** Two panels can hold entries over one cell, and the second undo must not
resurrect a value the first user's later change replaced.

1. **Every cell carries a monotonic revision.** A shared change records the revision it
   wrote.
2. **A direction is applicable only if every cell it would restore still sits at the
   revision that entry wrote.** Otherwise the whole operation fails with `HistoryConflict`
   (a `HistoryError`) and changes nothing — no partial restore, in either half.
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
  dependency mechanism and a shared handle is a dependency; a second context system would
  be the actual mistake.
- Nearest-provider shadowing already gives a preview or sandbox subtree its own binding.
- Render-time `inject()` semantics do not change here.

```python
PREFERENCES = sl.ContextKey[Preferences]("preferences")

class Dashboard(sl.Component):
    def render(self) -> sl.Node:
        self.provide(PREFERENCES, self.preferences)
        return self.embed(Content(), key="content")
```

The context key is typed by the namespace class, so `inject(PREFERENCES)` returns
`Preferences` and a subtree cannot mistake one binding for another.

## Phases

| # | Deliverable | Exit criteria |
|---|---|---|
| 0 | Restructure `transaction()` around a fallible commit; add `ActionParticipant`/`join_action`. | Two participants both prepare before either applies; a rejected prepare applies nothing, aborts every participant and restores local state, notifying no owner; a raising hook leaves the action committed and reported. Testable with no store. **Shipped.** |
| 1 | `Shared[ScopeT]`, `cell`, `Scope`, `SharedStore`; attribute read/write/`del`; immediate-outside-an-action behaviour; the declaration check; scope lifetime. | Descriptor identity, defaults, per-scope independence, equal-value no-op, reserved and mutable-typed names raising at class creation, an unhashable or unfrozen scope raising at construction, `discard` sweeping every class at one address, and drop-on-last-handle bucketed per `(class, scope)`. |
| 2 | Overlays, read-your-writes, read-revision tracking, prepare/apply. | A raising handler leaks no staged value; read-and-write conflicts raise `SharedConflict`, read-only actions do not; ABA is caught; a later write does not clear the guard. |
| 3 | Render observation, `topics`, stage-time follow reconciliation, publication on commit. | Two mounts react to one commit, once each; a dropped conditional read stops refreshing; no follow outlives its mount. |
| 4 | Shared deltas in history, evolving guards, validating `can_undo`, `HistoryConflict`, the reservation if §8a's test justifies it. | One entry undoes local plus multi-scope shared state; an intervening write disables the control and refuses the press; the inverse race produces no partial world. |
| 5 | Docs, conflict diagnostics, devtools scope/cell/revision inspection, the worked example. | Examples cover one class across scopes, deep injection, provider shadowing; the leak test holds under repeated bind/discard. |

## Verification

- `packages/squid-layouts/tests/test_transactions.py`: the phase-0 fix — participant
  prepare/apply ordering, a failing prepare applying nothing, a raising hook leaving the
  action committed and notified.
- `packages/squid-layouts/tests/test_shared_store.py` (new): descriptor identity and scope
  keying, including two namespaces at one address not colliding; defaults and `del`; the
  equality no-op; a `list`-typed cell and a reserved name each raising at class creation;
  an unhashable and an unfrozen scope raising at construction; immediate writes outside an
  action; staging, read-your-writes and rollback inside one; a read-and-write conflict
  including A→B→A; a read-only action not conflicting; guard stickiness after a later
  write; a live handle of one class *not* keeping another class's cells alive at the same
  address; `discard` sweeping both; and a bind/discard loop that ends with no cells.
- `packages/squid-layouts/tests/test_shared_reactivity.py` (new): a read outside a render
  records no dependency; observation reconciliation across renders; two mounts refreshed
  once each by one commit through a real bus and `drain()`; a discarded staged render
  leaves no permanent follow.
- `packages/squid-layouts/tests/test_history.py`: one entry spanning local and two scopes;
  an intervening write making `can_undo` false and the press raise `HistoryConflict` with
  nothing changed; guard evolution across undo → redo → undo; the §8a race.
- `test_public_api.py`: the new exports, and the no-discord import check extends to the
  store module — it is portable core.
- `just typecheck` (compare against a pre-change run; the tree is not at zero) and
  `git diff --check`.

## Consumers

The library user, per the productization standard — the same answer
[26](26-topic-bus.md) and [27](27-snapshot-stores.md) gave, and for the same reason. The
bot is not the audience and an in-tree consumer is not a gate on any phase.

What the bot does owe is the worked example and the test suite, which is where a design
error actually surfaces. The candidate is the settings panel's theme and locale reaching
a second live panel: small, obviously view-owned, and it exercises scopes, cross-mount
reads, and one history entry spanning both halves. If writing it turns out awkward, that
is a finding about the API, not a reason to wait for a consumer.

## Rejected alternatives

- **Free-standing `sl.atom("theme", ...)` keys and an untyped `SharedState` handle.** The
  first shape of this plan. `Atom[T]` typed the value but nothing typed the handle, so
  `preferences.get(SELECTED)` typechecked and silently returned a default — §2's
  no-absence-API is what made it silent, and the two decisions composed badly. The
  mitigation on offer was a naming convention ("name bindings for their role"), which is
  what you write when the type cannot say it. It also duplicated every name as a string
  that could drift from its variable. A class says all of it structurally.
- **`update(cell, fn)`, `expect(cell, expected)` and `compare_and_set`.** All three are
  derivable from read-and-write (§5b), and none of them survives attribute access
  gracefully: with no key to pass, they read `preferences.expect(Preferences.theme, ...)`,
  which names the class redundantly and breaks the illusion that a cell is an attribute.
  CAS had a second problem — inside a transaction its `True` would mean "valid right now,
  may still raise at commit", which is not what CAS means anywhere else.
- **Freezing reads, validating writes, or proxying cells.** §4 in full: the first lies
  about the declared type and only covers three builtins, the second pays on the hot path
  to reject what the declaration already described, and the third puts partial mutations
  behind revision guards that cannot see them. The declaration plus a class-creation check
  does the same work for nothing.
- **A separate `watch()`.** With one way to read a cell, the render-observation collector
  keys off the context the read happens in, which is information the runtime already has.
  A second reader would have let a render read a cell without recording a dependency —
  silent staleness, and the mistake nobody would find.
- **A namespace discriminator inside the address** (`Scope("prefs", user.id, guild.id)`).
  It did nothing for keying, because a cell is `(descriptor, scope)` and descriptors are
  class-unique. Its only real effect was to separate lifetime buckets, which §3 now does
  per `(class, scope)` — correctly, and without a hand-maintained string that has to agree
  across every construction site.
- **Address fields declared on the `Shared` class**, bare annotations beside marked cells:
  `user_id: int` next to `theme = sl.cell(...)`. It reads well and it is the natural use
  of the annotation slot the cells left free. It loses on machinery and on `discard`:
  `Shared` would have to generate an `__init__` from annotations, reimplementing part of
  `@dataclass` for a closed set of fields, and `store.discard(session_id=x)` would become
  a query across classes with different address shapes instead of a lookup. A declared
  frozen dataclass gets the same typing from an idiom the author already knows.
- **Designed-for multi-store transactions.** Nothing has produced a case where two stores
  take part in one action. The participant loop is a list, so it falls out and one test
  pins the atomicity property; no cross-store guarantee beyond prepare-all-then-apply-all
  is documented, and none is designed for.
- **A weak subscriber index inside the store.** A second copy of `TopicBus`'s coalescing
  and delivery contract, with a second set of failure modes. The store publishes; the bus
  delivers.
- **Payloads on the bus.** Still rejected, for 26's reasons, in full. Cell addresses are
  addresses.
- **Reference-copy cells (`copy="ref"`).** `sl.state()` has it for services and guilds
  that cannot be copied. A shared cell holding an uncopyable collaborator is a service and
  should be injected as one; §4's model has no room for it and needs none.
- **Snapshot isolation, or a public `atomic()` / lock.** Read-committed plus the §5b guard
  keeps every wait visible. A public lock in a handler is a deadlock in a UI.
- **Automatic retry on conflict.** Unsafe for handlers with external effects, and the
  framework cannot identify them.
- **Hierarchical scope fallback** (guild scope backing user scope). Real, and cleanly
  expressible today by holding two handles and choosing. Encoding precedence in `Scope`
  makes every read a search and every conflict ambiguous about which cell it means.
- **Persistence.** Still deferred, but no longer structurally blocked, and that changed
  with the class. Free-standing atoms were identity-keyed with no stable name, so the
  store was unserializable without a registry; `Preferences.theme` is a stable key by
  construction, and a `Scope` of serializable parts is a serializable address. What
  remains is a real design question rather than a missing mechanism: §3's lifetime model
  says a scope nobody holds is gone, and durable view state means something must decide
  what outlives that. Not v1.
- **Reducers, middleware, dispatch, a global singleton.** 90's rejection, unchanged.
