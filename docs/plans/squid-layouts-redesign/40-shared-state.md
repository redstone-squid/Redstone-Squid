# 40 — Shared state: view state that outlives one mount

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
package-side. This plan does not overturn it. **There is no store here** — no registry, no
keyed lookup, no container the host installs. A shared namespace is an object; components
that share state hold the same object, the way they already share a service.

What is added is narrower than a store and is the part 90 never had an answer for: that
object's writes join the action's transaction, and its changes reach the bus and
[28](28-history.md) without the author writing either half.

| 90 rejected | Status here |
|---|---|
| dispatch → middleware → reducers → subscribers | Rejected. Reads and writes are direct, in `sl.state()`'s shape. There is no dispatch and no interception point. |
| A global singleton store | Moot. There is no store to be a singleton of; a namespace is constructed by whoever owns it. |
| A second source of domain truth | Rejected. `Controlled`/`Managed` ([10](10-selection-ownership.md)) still governs ownership, and §3's lifetime model makes a namespace an unsuitable place to keep anything durable. |
| Payloads on the bus | Rejected, and untouched. The bus still carries addresses; a namespace publishes cell addresses through it and subscribers re-read. |

## Design

> A shared namespace holds what the view owns. Anything the application would still want
> if no one were looking at it belongs to the application's data layer.

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

here = Member(user.id, guild.id)
preferences = Preferences(bus, here)              # the host's TopicBus, per §7
workspace = Workspace(bus, here)

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

`sl.cell(default)` is `sl.state(default)` one level out: the same
declared-type-is-the-real-type signature (`cell(default: ValueT) -> ValueT`), the same
`factory=` alternative, the same `opaque=` escape hatch, the same descriptor mechanics —
and, since [41](41-reactivity-cells.md), literally the same storage. What differs is who is
told when it changes; where a write goes while an action is in flight is now the same too.

| Expression | Semantics |
|---|---|
| `handle.cell` | Read. Inside an action: this transaction's staged value if it has one, else the latest committed value, and the value read is remembered (§5b). Inside a render: also records a render dependency (§7). Outside both: a plain read. |
| `handle.cell = value` | Write. Staged inside an action, committed immediately outside one. |
| `del handle.cell` | Reset to the declared default. A write, and never a read, so never guarded. |
| `handle.topic(Cls.cell)` | The cell's bus address, for a host that wants to follow it by hand (§7). |
| `handle.scope` | The label this namespace was constructed with, typed (§2). |

That is the whole surface. There is no `get`, `set`, `watch`, `update`, `expect` or
`compare_and_set`; *Rejected alternatives* records why each is absent.

**A namespace is a real type.** A component that wants preferences asks for `Preferences`,
`sl.ContextKey[Preferences]` says which binding it means, and reading a workspace cell off
a preferences handle is an attribute error before it is anything else. `sl.Shared[Member]`
types `handle.scope` the same way.

**Sharing is passing the handle.** There is no lookup that two panels can both perform to
converge; they converge because something gave them the same object. §9 is that mechanism
in full, and it is the mechanism the package already has.

### 2. Cells and namespaces

**A namespace holds the same `_Cell` a component does.** This plan originally reached for
`ReactiveOwner` — a `__dict__`, `_state_changed(names)`, `_state_rolled_back()` — so that a
namespace could inherit the component-state machinery rather than grow a parallel copy.
[41](41-reactivity-cells.md) §6 took that further than the conformance: with component
state already stored in a cell that stages through the transaction and tracks its own reads,
the two are not similar designs, they are one.

```text
_Cell           value, version; staged through the transaction; read-tracked
  owned by a Component   -> a write calls owner.invalidate()
  owned by a Shared      -> a write publishes (owner, descriptor) on the bus
```

Everything else — immutability, tracking, staging, rollback, `StateChange`, `StateDelta`,
the undeclared-write report — is the same code, and the only thing a namespace declares is
what a write does after it lands.

**A computed may depend on a shared cell.** `self.workspace.selected` read inside an
`@sl.computed` records that cell as a source and recomputes when another panel writes it.
That falls out of tracking rather than being designed for: a read is a read, whoever owns
the cell.

**Cell identity is descriptor identity.** `Preferences.theme` and `Workspace.theme` are two
cells because they are two descriptors, and no rule has to say so — it falls out of the
declaration the way `ContextKey`'s `eq=False` identity does.

**The diagnostic name comes free.** `__set_name__` gives the attribute name and the owning
class, so history labels, conflict messages and devtools say `Preferences.theme` with
nothing declared twice and nothing that can drift from the variable it is assigned to.

**Defaults are values.** No `has()`, no `Unset`, no absence protocol. If absence means
something it is a value in the declared type — `None`, or a member of the enum.

**The scope is a label, and nothing is required of it.** `Shared` is generic in it so
`handle.scope` is typed, and that is the entire contract: not frozen, not hashable, not
validated, not consulted. Nothing keys on it, because nothing keys on anything — a cell is
reached through the object that holds it. A frozen dataclass is the pleasant shape and the
examples use one, but a bare `int`, a mutable record or nothing at all are all legal.

`sl.Shared` unparameterised means `sl.Shared[None]`, for a namespace with nothing to say
about itself.

What the label buys is diagnostics. `Preferences(Member(1, 2)).theme` in a devtools row, a
conflict message or a history label is worth the four lines of dataclass, and it is worth
them only there.

**A few attribute names are reserved** — `topic`, `scope`, `bus`, and anything beginning
with an underscore. Declaring a cell with one of those raises at class creation.

### 3. Lifetime: the handle is the state

A bot process runs for weeks across thousands of guilds, and the first draft of this plan
spent its longest section on not leaking: weak references per `(class, scope)` bucket, a
`discard(scope)` sweep, a `bindings()` view so the leak was observable, and a documented
hazard that a premature drop reads back as defaults and looks like normal operation.

All of it was the cost of a keyed store. With the cells in the handle, **a namespace lives
exactly as long as the object does, and Python already knows how to do that.**

The bus does not extend it. `TopicBus._forget_if_idle` (`topics.py:328`) pops a topic once
it has no subscriptions, nothing queued and nothing in flight, so a topic naming a handle
stops referencing it as soon as the last mount unfollows. A queued delivery holds it until
the next drain, which is bounded and is exactly as long as it should.

**Retention is a policy, and holding the handle is how you express it.** The three
motivating examples in *Problem* want two different lifetimes, and the difference is
visible in one line of host code:

- **Co-existence state** — a selection shared between a list and the detail view, two
  panels agreeing on a filter. The panels hold the `Workspace`; when the last one finishes,
  it is gone. That is correct: nothing was looking at it.
- **Retention state** — preferences. The *session* holds the `Preferences`, not the panels,
  and it survives every panel opening and closing. A settings panel that closes does not
  reset the theme, because it was never the owner.

Getting this wrong is the one failure mode left, and it is a visible one: state that
should have persisted reads back as its declared default. There is no absence API (§2), so
it looks like a scope nobody ever wrote. The mitigation is that the owner is now a single
obvious line in host code rather than a lifetime rule inside the package.

### 4. Values: cells behave like component state

`workspace.filters = (*workspace.filters, tag)`, and other mounts see it.

This section has been rewritten twice and the principle survived both times: **a shared cell
behaves like component state, whatever that turns out to mean.** The first draft required
cells to be declared immutable while `sl.state()` proxied, so the same line was a real write
on one and a silent no-op on the other; the second draft reversed itself and reused the
proxies for exactly that reason. [41](41-reactivity-cells.md) removed the proxies, so parity
now points back the other way — and this time it costs nothing, because both sides move
together rather than one side being asked to carry a rule the other does not.

So: a cell value is immutable, checked with `hash()` at the write, and `opaque=True` is the
escape hatch for a collaborator the namespace holds and never mutates. A mapping that has to
be a cell is an `sl.FrozenMapping`.

**Nothing copies.** The copy-on-read the proxy version needed — materialising a deep copy
into the overlay so that rollback stayed "drop the overlay" and §5b's guard still compared
against an untouched committed value — has nothing to do. An immutable value *is* its own
snapshot: the overlay holds a reference to the committed value and a reference to the staged
one, and both properties hold by construction.

**Mutating during a render is not a case any more.** There is nothing to mutate. A write
during a render is still wrong for the reason the proxy rule gave — it would publish
halfway through producing a tree — and `__set__` is where that is caught, not a proxy.

**Equality short-circuit.** Assigning an equal value is a no-op: no publish, no history
change. "Equal" means `is`, then `==` inside a `try` that treats a raising or non-boolean
comparison as *not* equal. 41 made this the rule for component state too, so the two agree;
an `opaque=` cell compares by identity alone, because `==` on a collaborator is the author's
code rather than a cheap settled-value check.

### 5. Actions: staging, one commit

Writes inside an action stage into a per-namespace overlay and become visible together, or
not at all. Outside an action they commit synchronously.

The overlay was the one thing a namespace could not inherit from `_State`, which wrote
through and relied on a snapshot to undo it. That was safe only because nothing else can
observe a component's state mid-action, and it stopped being safe the moment the cell was
shared: a shared write crossing an `await` would publish a dirty read that a later rollback
retracts. [41](41-reactivity-cells.md) §4 moved component state onto the overlay as well, so
this is no longer a difference between the two — it is how a cell works.

Guarantees, in the vocabulary the reader already has:

- **No dirty reads.** Another action never sees staged values.
- **Read-your-writes.** An action sees its own.
- **Read committed.** Two reads either side of an `await` may differ. Documented, not
  defended against; snapshot isolation is not on offer.
- **Atomic publication.** One action's shared writes appear together, with its local state
  writes, at one commit.

#### 5a. The transaction participant seam

Phase 0 shipped this, and it is worth reading `_Transaction.commit`
(`runtime/reactivity.py:228-252`) before the rest of this section. It also closed a real
bug: `commit()` used to run outside the `try`, so anything raising inside it — a commit
hook, and `History._push` invalidates its owner from one — propagated with no rollback,
leaving the action's writes standing and no owner notified.

The sequence, with participants:

```text
ACTION
  local sl.state writes           -> the transaction's snapshots, unchanged
  shared writes                   -> a per-namespace overlay, enlisted with join_action()

COMMIT  -- fallible half, nothing visible yet
  1. PREPARE each participant      validate the read-and-write guards; freeze the
                                   prepared writes
  2. CONTRIBUTE each participant   the StateChange tuple describing what it will publish
  3. BUILD the state delta         local snapshots plus every contribution
     any failure above             abort every participant, restore local state,
                                   raise (SharedStateConflictError, for a namespace)
COMMIT  -- published; the action has happened
  4. APPLY each participant        synchronous, no awaits, no failure paths left
  5. NOTIFY owners                 _state_changed, as today -- for a namespace, publish
  6. FINALIZE each participant
  7. RUN commit hooks              recorders, i.e. sl.history
```

Two orderings are load-bearing.

**Everything fallible happens before step 4.** That is the whole reason prepare exists, and
it is what lets `transaction()` roll an action back after its handler returned cleanly.

**Commit hooks run last, after publication.** A recorder's effect leaves the transaction —
`sl.history` pushes an entry onto a stack that outlives it — so a hook must not run
anywhere a later failure could still roll the action back.

`contribute()` is the one addition to the shipped `ActionParticipant` protocol
(`runtime/reactivity.py:24-50`), and it defaults to `()` so it does not break an existing
implementation. §8 is the reason it exists.

The seam is public: `sl.ActionParticipant` and `sl.join_action(key, factory)`, alongside
the `sl.on_action_commit` it sits next to. A namespace enlists itself under `key=self`, the
way `History` already does at `runtime/history.py:123`, so one action writing three
namespaces has three participants and gets prepare-all-then-apply-all across them for free.

#### 5b. Conflicts: one rule, derived from what the action did

> **A cell that a transaction both reads and writes carries the value it read as a commit
> precondition. Everything else is blind.**

```python
async def toggle(self, event: sl.ActionEvent) -> None:
    self.preferences.theme = flip(self.preferences.theme)   # read + write -> guarded
    self.preferences.locale = Locale.EN                     # write only   -> blind
```

That is compare-and-set, `update()` and `expect()` all three, derived from what the code
did rather than declared alongside it. `workspace.filters = (*workspace.filters, tag)` is a
guarded read-modify-write with no ceremony, and the author cannot forget to ask for the
guard, because asking for it is not a separate act.

**Read *and* write, not read alone.** A handler that reads a shared cell to compose a
message and never writes it records nothing; guarding on reads alone would make every
read-only action conflict-prone, which is a false positive with no lost update behind it.
Prepare validates only the intersection of the read set and the write set.

**Only reads that reached committed state count.** A read that returns this action's own
staged value answers "what did I just write", not "what is the world", so it does not enter
the read set. The set is therefore the cells the action *observed*, which is the set the
guard is about.

**Value, not revision.** The precondition is "this cell still holds what I read", compared
with §4's conservative equality. A revision counter would additionally catch A→B→A, and
that was the first draft's choice; it is a false positive. A write computed from A, landing
on a cell that holds A, has lost nothing. 41 gives every cell a version as well, and this
guard still does not use it, for the same reason.

**The guard is the first value read**, and a later write does not clear it: the action
already branched on what it read.

**Outside an action there are no guards.** There is no transaction to hold a read value, so
every immediate write is blind and last commit wins.

What this costs: an action cannot observe a cell's committed value and then deliberately
overwrite it blind. There is no ordering that gets around it — writing first makes the
later read return the staged value, which is not an observation of the world. The failure
is a retryable conflict rather than corruption, and it buys the removal of three
operations.

### 6. What a conflict looks like

`SharedStateConflictError` names the namespace, its scope and the cell, using the cell's
`Owner.attribute` diagnostic name. The suffix is not decoration: every failure in the tree
carries it, from `errors.py` through `ReactiveWriteError` and `ResourceNotReadyError`.

**A handler cannot catch it.** `transaction()` wraps the handler at `mount.py:1509`, so
prepare runs when the `with` block exits — after the handler body has returned. A
`try/except` around a write catches nothing, and the doc must not suggest one.

A conflict therefore travels the ordinary failed-handler path: the transaction rolls back,
nothing is published, and the mount's `handle_error` shows what it shows for any raising
handler. `Chrome` gains `changed_elsewhere` so the default message says something true
about the cause.

An application that wants more has the seam it already has for application-wide action
policy — `sl.ActionMiddleware`, which wraps the transaction rather than sitting inside it:

```python
class ExplainConflicts(sl.ActionMiddleware):
    async def dispatch(self, request: sl.ActionRequest, proceed: sl.ActionProceed) -> None:
        try:
            await proceed()
        except sl.SharedStateConflictError:
            await request.event.notice(chrome.changed_elsewhere)
```

**There is no automatic retry.** Re-running a handler that may have already sent a message
or written a row is not safe, and the framework cannot tell which handlers those are.

### 7. Reactivity through the bus

The package has exactly one cross-mount refresh mechanism and should keep having one. A
namespace does not grow a subscriber index; it **publishes cell addresses on the `TopicBus`
it was constructed with.**

1. A cell's address is the `(handle, descriptor)` pair. `topics.py:20` declares
   `type Topic = Hashable` and both halves hash by identity, so nothing has to be encoded
   into a string — no canonical form to get wrong, no collision surface invented on the way
   to the bus. `handle.topic(Preferences.theme)` is the one constructor, for a host that
   wants `reactor.follow(mount, preferences.topic(Preferences.theme))` by hand.
2. During render, a cell read records its address into a render-observation collector.
   [41](41-reactivity-cells.md) made tracked reads the package's one mechanism, so this is
   that mechanism with a render as the consumer rather than a new one. This is why there is
   no separate `watch()`; the context the read happens in is what distinguishes a render
   dependency from a transaction read, and both are just reading the attribute.
3. The rendered result carries the deduplicated address set, and the mount reconciles its
   follows against it through `Reactor.follow`.
4. Commit step 5 publishes the addresses whose cells actually changed, from
   `Shared._state_changed`. Coalescing, at-most-one-drain-per-topic and the delivery
   contract are the bus's, already tested.

**Reconcile at stage time, not after delivery.** `Reactor.follow`'s own docstring
(`discord/reactor.py:158`) gives the reason: a write landing between a mount's read and its
subscription is lost, and the bus is not durable, so that panel is stale until someone
clicks it. A staged render that is later discarded leaves a subscription the next
successful render removes, and the worst case is one spurious refresh. Over-subscribe;
never under-subscribe.

**Where the halves live.** The descriptor, the collector, the address and the publish call
are portable core and import no Discord. The reconciliation is not: `Reactor`
(`discord/reactor.py:155`) and `Mount` (`discord/mount.py:445`) are both Discord-side, and
so is the code in step 3. Phase 3 lands in two packages and its tests do too.

**One constraint, stated rather than sold.** An identity-keyed address cannot be
serialised, so it cannot be published from another process — `topics.py:107` contemplates
bridging an external change feed into `publish()`, and cell addresses are outside that. For
view state that is the right trade; it would not be for anything durable, which §3 already
says a namespace is not.

The constructor argument is a real bus, not an optional one. A `bus=None` default would
mean two notification paths and a namespace that silently stops being reactive. Tests
construct a `TopicBus` and call `drain()`, which is what that seam is for.

### 8. History

Shared cells are framework-owned state, so one `HistoryEntry` covers an action's local
writes and its shared writes across every namespace it touched. The author does not owe an
inverse for state the framework can restore just because it crossed a mount boundary.

```python
async def select(self, event: sl.ActionEvent, build_id: int) -> None:
    self.open = True
    self.workspace.selected = build_id
    self.history.record(f"Select build {build_id}")
```

**Undo restores a shared cell blindly.** It is a write, and §5b already says a write-only
operation carries no precondition and that last commit wins. Undo reads nothing; it
restores a value the entry recorded. Applying the existing rule is the whole design, and it
is why this section has no conflict model of its own.

The first draft had one, and it was the reason to revisit: every cell an entry touched
carried the revision it wrote, any intervening change made the direction inapplicable, and
`can_undo` returned false. Two panels sharing a selection — the plan's own motivating
example — conflict on that rule as a matter of course. Worse, because an entry spans local
and shared writes and refuses as a unit, one stale shared cell disabled undo of the entry's
local half too, and the stack below it with it. An undo stack that a sibling panel can
brick is not a partly-working undo stack.

The cost of blind restore, stated plainly: undo can revert a value another panel set more
recently. That panel re-renders and sees it, which is what shared state means in every
other case — and §3 forbids anything durable living here, so what is lost is a filter or a
selection that its owner can set again.

**The mechanism.** A namespace is a `ReactiveOwner`, so `StateChange` and
`StateDelta._apply` (`runtime/reactivity.py:120-131`) already work on one unmodified:
`_restore` writes the `__dict__`, `_after` marks it changed, and `_state_changed` publishes.
Undo therefore reaches other panels for free.

What was missing is the way in. `_Transaction.delta()` (`:203-226`) builds from local
snapshots and reads after-values out of `owner.__dict__`, which — with staging — still hold
the old values when the delta is built. So the participant supplies its own changes
instead: `contribute()` returns them, built from the overlay, which knows both halves
without touching `__dict__`. `StateDelta` itself is unchanged.

Two details worth writing down:

- `History.undo` restores synchronously, after its `await inverse()` returns
  (`runtime/history.py:130-132`). The restore bypasses the overlay and writes through, but
  there is no await inside it, so it opens no dirty-read window.
- It restores inside a `transaction()`, so the restore is itself rollback-safe and its
  publishes coalesce at that transaction's commit.

**`History._reverse` ordering is unchanged**, and now uncomplicated. It runs the author's
world inverse first so a failed one leaves the reader's view alone. The first draft needed
cell reservations to keep a postflight guard from failing after the world was already
reversed; with no guard there is no postflight, no race, and no reservation.

### 9. Composition

This section used to say "unchanged, and deliberately so". It is now the entire sharing
mechanism, because §1 removed the alternative: two panels converge on one namespace only
because something handed them the same object.

- Constructor injection at ownership boundaries is the default.
- `ContextKey` + `provide()`/`inject()` is the prop-drilling escape hatch. It is a
  dependency mechanism and a shared handle is a dependency; a second context system would
  be the actual mistake.
- Nearest-provider shadowing already gives a preview or sandbox subtree its own binding.
- Render-time `inject()` semantics do not change here.
- A host that wants get-or-create per member keeps a dict of handles. That dict is §3's
  retention policy written down, and it belongs to the host that knows the lifetime.

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
| 0 | Restructure `transaction()` around a fallible commit; add `ActionParticipant`/`join_action`. | Two participants both prepare before either applies; a rejected prepare applies nothing, aborts every participant and restores local state, notifying no owner; a raising hook leaves the action committed and reported. **Shipped.** |
| 1 | `Shared[ScopeT]`, `sl.cell` over 41's `_Cell`; `__setattr__` reporting; attribute read/write/`del`; immediate-outside-an-action behaviour. | Descriptor identity; defaults; two namespaces with same-named cells not colliding; equal-value no-op; reserved names raising at class creation; an unhashable, mutable and absent scope all accepted; a mutable cell value refused and an `opaque=` one accepted; an undeclared write raising. |
| 2 | `contribute()`, prepare/apply, `SharedStateConflictError`. Staging and read tracking come from 41. | A raising handler leaks no staged value; read-and-write conflicts raise, read-only actions and staged-value reads do not; a later write does not clear the guard; A→B→A does **not** conflict; one action across three namespaces prepares all before applying any; a `@sl.computed` over a shared cell recomputing when another owner writes it. |
| 3 | Render observation, `topic()`, stage-time follow reconciliation, publication on commit. Core and Discord halves. | Two mounts react to one commit, once each; a dropped conditional read stops refreshing; no follow outlives its mount; a write during a render raising. |
| 4 | Shared changes in `StateDelta`; blind undo and redo. | One entry undoes local plus multi-namespace shared state in one press; a sibling panel's intervening write does not disable the control and does not fail the press; undo publishes to the sibling; an entry with an external inverse whose inverse raises restores nothing. |
| 5 | Docs, conflict diagnostics, devtools namespace/cell inspection, the worked example. | Examples cover deep injection, provider shadowing, and both retention shapes from §3; a namespace dropped by its last holder is collected. |

## Verification

- `packages/squid-layouts/tests/test_transactions.py`: `contribute()` reaching the delta;
  the default `()` leaving an existing participant unaffected; contribution failure
  aborting before publication.
- `packages/squid-layouts/tests/test_shared_state.py` (new): descriptor identity; defaults
  and `del`; the equality no-op; reserved names raising at class creation; scopes that are
  unhashable, mutable and absent all working; immediate writes outside an action; staging,
  read-your-writes and rollback inside one; in-place mutation of a `list` cell publishing,
  and rolling back; a read-and-write conflict; A→B→A **not** conflicting; a read-only
  action not conflicting; a read of a staged value not entering the read set; guard
  stickiness after a later write; one action spanning three namespaces; a namespace with no
  live holder collected after its last mount finishes.
- `packages/squid-layouts/tests/test_shared_reactivity.py` (new, core half): a read outside
  a render records no dependency; observation reconciliation across renders; a mutation
  during render raising; publication through a real bus and `drain()`.
- `packages/squid-layouts/tests/discord/test_shared_follow.py` (new, Discord half): two
  mounts refreshed once each by one commit; a discarded staged render leaves no permanent
  follow.
- `packages/squid-layouts/tests/test_history.py`: one entry spanning local and two
  namespaces; a sibling's intervening write leaving `can_undo` true and the press
  succeeding; undo publishing to the sibling mount; guard-free undo → redo → undo.
- `test_public_api.py`: the new exports, and the no-discord import check extends to the
  shared-state module — it is portable core.
- `just typecheck` (compare against a pre-change run; the tree is not at zero) and
  `git diff --check`.

## Consumers

The library user, per the productization standard — the same answer
[26](26-topic-bus.md) and [27](27-snapshot-stores.md) gave, and for the same reason. The
bot is not the audience and an in-tree consumer is not a gate on any phase.

What the bot does owe is the worked example and the test suite, which is where a design
error actually surfaces. The candidate is the settings panel's theme and locale reaching a
second live panel: small, obviously view-owned, and it exercises §3's retention shape (the
session holds it, not the panel), cross-mount reads, and one history entry spanning both
halves. If writing it turns out awkward, that is a finding about the API, not a reason to
wait for a consumer.

## Rejected alternatives

- **A scope-keyed store.** The previous shape of this plan: a host-owned `SharedStore`,
  cells keyed `(descriptor, scope)`, handles constructed as `Preferences(store, Member(...))`
  by anyone wanting to converge on the same state. It was a lookup table nothing looked up
  — §9 was already the sharing mechanism, and no example needed two panels to converge
  without a shared reference. Removing it removed everything it had dragged in: the frozen
  and hashable requirements on a value the store never interprets, a whole lifetime
  subsystem (weak references per `(class, scope)`, `discard`, `bindings()`, a documented
  silent-premature-drop hazard) to solve a leak that object lifetime solves, and a
  co-scoping hazard where two namespaces at equal addresses were swept together. Reference
  counting handles that were themselves reachable only through other objects was work with
  no beneficiary.
- **Free-standing `sl.atom("theme", ...)` keys and an untyped `SharedState` handle.** The
  first shape of this plan. `Atom[T]` typed the value but nothing typed the handle, so
  `preferences.get(SELECTED)` typechecked and silently returned a default — §2's
  no-absence-API is what made it silent, and the two decisions composed badly. It also
  duplicated every name as a string that could drift from its variable. A class says all of
  it structurally.
- **`update(cell, fn)`, `expect(cell, expected)` and `compare_and_set`.** None survives
  attribute access gracefully: with no key to pass, they read
  `preferences.expect(Preferences.theme, ...)`, which names the class redundantly and breaks
  the illusion that a cell is an attribute. They are also unnecessary — §5b derives the
  guard from what the handler did, and §8's blind restore means nothing internal needs an
  explicit precondition either. CAS had a third problem: inside a transaction its `True`
  would mean "valid right now, may still raise at commit", which is not what CAS means
  anywhere else.
- **Requiring immutable cell declarations *by annotation*.** The previous shape of §4:
  `list`, `dict` and `set` annotations rejected at class creation. The conclusion was right
  and [41](41-reactivity-cells.md) adopted it; the mechanism was not, and 41 rejects it for
  the reasons this plan already found. `__set_name__` does not receive annotations, and
  reaching `owner.__annotations__` during class creation forces PEP 649 evaluation of names
  the module may not have defined yet, in a package that bans quoted forward references
  precisely to rely on that laziness. And the argument it used against freezing reads ("only
  ever partial: a dataclass whose field is a list sails straight through") applied to itself
  unchanged. `hash()` at the write is deep where an annotation is shallow, and it is what
  ships.
- **Freezing reads, or validating every write.** Still rejected, and these were always the
  better half of the old §4's case. Coercing `list` → `tuple` on the way out makes the
  declared type a lie, since there is no way to spell "frozen `T`". An immutability
  predicate at write time pays a pass over the value on the hot path to reject what the
  declaration already described.
- **Per-cell revisions.** A monotonic counter would make §5b's guard O(1) instead of a
  possibly-deep `==`, and would catch A→B→A. Neither earns it. The equality is the same
  conservative comparison `_Computed.refresh_for` runs on every computed refresh today, and
  ABA is a false positive for value state — a write computed from A landing on a cell that
  holds A has lost nothing. With undo blind (§8), nothing else wanted the counter.
- **Conflict-checked undo, and cell reservations.** §8 records why in full: the rule
  refused in the plan's own motivating scenario, and refusing as a unit let one stale shared
  cell disable an entry's local half and everything under it. The reservations existed only
  to close a race between a preflight check and an awaiting external inverse; with no check
  there is no race. The first draft already hedged them behind "ships only if the test
  justifies it", which was the tell.
- **A separate `watch()`.** With one way to read a cell, the render-observation collector
  keys off the context the read happens in, which is information the runtime already has. A
  second reader would have let a render read a cell without recording a dependency — silent
  staleness, and the mistake nobody would find.
- **A `handle.topics.cell` attribute namespace.** A proxy object, an unknown-name error path
  and a second place cell names appear, to save naming the class once. `handle.topic(
  Preferences.theme)` passes the descriptor, which is already the identity §2 keys on.
- **A weak subscriber index inside the namespace.** A second copy of `TopicBus`'s coalescing
  and delivery contract, with a second set of failure modes. The namespace publishes; the
  bus delivers.
- **Payloads on the bus.** Still rejected, for 26's reasons, in full. Cell addresses are
  addresses.
- **A shared cell as a place to keep a service.** `opaque=True` exists on both primitives
  and a shared cell inherits it, but a namespace holding an uncopyable collaborator is a
  service and should be injected as one. The hatch is there for the same reason it is on
  `sl.state()` — a value the owner holds and never mutates — not as a way to put a
  connection pool behind a bus topic.
- **Snapshot isolation, or a public `atomic()` / lock.** Read-committed plus the §5b guard
  keeps every wait visible. A public lock in a handler is a deadlock in a UI.
- **Automatic retry on conflict.** Unsafe for handlers with external effects, and the
  framework cannot identify them.
- **Hierarchical scope fallback** (guild preferences backing user preferences). Never a
  mechanism question, and less of one now that the scope is a label: hold two handles and
  choose. Encoding precedence would make every read a search and every conflict ambiguous
  about which cell it means.
- **Persistence.** Deferred, and *more* structurally blocked than the previous draft
  claimed. That draft argued the class had unblocked it — `Preferences.theme` is a stable
  key and a `Scope` of serializable parts is a serializable address. Neither survives:
  addresses are identity pairs (§7), and there is no address at all, only an object. Durable
  view state would need a host-supplied identity for each namespace and a policy for what
  outlives the object holding it, which is a design, not a missing mechanism. Not v1.
- **Reducers, middleware, dispatch, a global singleton.** 90's rejection, unchanged, and
  further from reach than before — there is no store for any of them to attach to.
