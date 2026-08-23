# 41 — Reactivity: pull-based cells, tracked reads, replaced values

## Problem

`sl.state()` observes mutation by proxying the value. That is the wrong bargain, and the
evidence is in the tree rather than in framework fashion.

### Two defects, both reproduced

`spikes/41/` holds the probe. Against the shipping package:

```text
1. top-level list append      -> invalidated: True
2. list inside a dataclass    -> invalidated: False
3. field on a dataclass       -> invalidated: False
4. dataclass proxied?         -> Filters          (passes through unwrapped)
5. top-level list proxied?    -> ReactiveList
```

`_observe` (`runtime/reactivity.py:614`) wraps exactly `list`, `dict` and `set`, and
recurses only through those. Every other object — dataclass, model, custom class — passes
through unproxied, so every mutation inside it is invisible. The guarantee holds for the
shape an author is least likely to get wrong and fails for the shape they are most likely
to reach for.

```text
6. @sl.computed(depends=(n,)) whose body also reads self.rows
   doubled (n=0, rows=['a'])  -> 1
   after rows.append('b')     -> 1    <- silently wrong, not merely stale
   after n = 1                -> 4    <- jumps when an unrelated dependency changes
```

`computed(depends=...)` (`:793`) and `resource(depends=...)`
(`runtime/resources.py:215-234`) take a hand-maintained tuple of descriptors. Omitting one
does not raise; it returns a wrong cached value until something else happens to
invalidate. A dependency that is conditional — `self.x if self.flag else self.y` — cannot
be declared correctly at all, only over-declared.

### The package already disagrees with itself

There are three dependency mechanisms here, and they do not agree.
`computed(depends=)` and `resource(depends=)` are hand-declared. `observe_resources()`
(`runtime/resources.py:246`) is a ContextVar collector that records what a render actually
touched. [40](40-shared-state.md) §7 adds a second collector for shared cells.

Every mechanism designed since the first one tracks reads automatically. Only the oldest
asks the author to maintain the graph by hand.

### And the proxies have no consumers

This is what makes the fix cheap, and it was checked rather than assumed:

```text
depends= call sites (excluding the definitions)      4
sl.state() declared with a mutable default/factory   0
in-place mutation of a declared state field          2   -- both in tests
ReactiveList/Dict/Set + _ReactiveMixin + _observe   183 lines (reactivity.py:441-623)
```

Across the 23 files that declare `sl.state()`, nothing in the package source and nothing
in `squid/` mutates a state field in place. The subsystem that exists to make mutation
observable is supporting a pattern that appears twice, in tests.

`copy="ref"` points the same way. Its 12 sites, all in `patterns/`, exist to *skip* the
deep copy for a value the author is treating as a reference. That is already a hand-rolled
declaration of immutability, written as a copying strategy.

### What this is not

Not fine-grained updates. Solid and Vue reach for signals largely to avoid re-running
components and to patch individual DOM nodes; the output here is one Discord message,
rendered whole, planned, diffed and sent as one edit. Whole-component invalidation is
already the right granularity and stays. **The case for this plan is tracking correctness
and deletion, not update cost.**

Not a compiler. An import hook or AST rewrite buys nothing Python's descriptor protocol
does not already give, and costs debuggability and type-checker support.

## Design

> A cell holds a value that is replaced, never mutated, and a version. A read is tracked. A computed recomputes
> when a version it read has moved. Nothing is pushed.

### 1. Values are replaced, never mutated, and the type checker holds the line

```python
class Panel(sl.Component):
    filters: Filters = sl.state(Filters())                 # frozen dataclass
    rows: Sequence[str] = sl.state([])                     # a list in, a Sequence out
    channels: Mapping[str, int | None] = sl.state({})

panel.rows = (*panel.rows, "a")                            # replacement
panel.channels = {**panel.channels, "log": 1}
panel.filters = replace(panel.filters, limit=25)

panel.rows.append("a")                                     # pyrefly: Sequence has no append
bad: list[str] = sl.state([])                              # pyrefly: Sequence is not a list
```

Nothing in the cell machinery needs a value to be immutable. A write bumps a version,
rollback holds the previous reference, the settle short-circuit uses `==`. What the design
needs is that a held value is never **mutated in place**, because an in-place change moves
no version. That is a discipline, and the question is only who enforces it.

This plan first shipped with runtime enforcement — `hash()` at every write, which is deep
and unconditional, and which Python makes expensive in exactly one place: there is no
literal for a frozen mapping, so every mapping write site paid an `sl.FrozenMapping(...)`
wrapper (see *Rejected alternatives*). It now enforces it statically. `state()` is overloaded
so that a `dict[K, V]` default or factory declares `Mapping[K, V]`, a `list[T]` declares
`Sequence[T]`, a `set[T]` declares `AbstractSet[T]`, and anything else declares `T` as
before. Two things follow. A concrete annotation — `rows: list[str]` — is a type error at the
declaration, because a `Sequence` is not assignable to a `list`. And every mutating method
is a type error at the use. The stored value is the one assigned: no wrapper, no copy, no
freezing at the boundary, so the declared type is the real value's read-only view and
`self.channels["log"]` stays plain attribute access.

Coverage is honest. The three builtins and their subclasses are enforced at the declaration.
A custom class or a dataclass with a `list` field falls through to `T`, where the checker
enforces whatever that type's own annotations say. A user without a type checker has no
guard, which is the contract every typed Python library offers, and the population it
guards against is measured in *Problem*: two in-place mutations in the tree, both in tests.

**Nested updates are a local.** `{**m, k: v}` covers a one-key replacement, and every
in-tree write is one. For more than that, copy into a local, mutate it, assign it back:

```python
channels = dict(self.channels)
channels["log"] = 1
del channels["audit"]
self.channels = channels
```

Typed end to end — the local is a `dict`, the field a `Mapping` — and the last line is the
ordinary write. A `draft()` context manager was built and deleted: it saved that one line and
cost a string naming the field so the framework could do the assignment.

**`opaque=True` replaces `copy="ref"`.** A service, a guild, a `_WindowRequest` — a
collaborator the component holds and does not mutate — is declared for what it is:

```python
_request: _WindowRequest = sl.state(_WindowRequest(), persist=False, opaque=True)
```

It settles on identity rather than `==`, because `==` on a collaborator is the author's
code, and it is never persisted. The promise sits at the declaration where a reader will
see it; what it stops meaning is a copying strategy, since no snapshot copies anything any
more.

A collaborator is the one thing that legitimately changes in place, and `mutated` is how the
component says so. It takes the object, not a field name: identity finds the field, which is
how an opaque field settles anyway, so the call is typed and there is no string to drift.

```python
self.build.door_orientation = event.selected[0]
self.mutated(self.build)          # version moves, computeds over `build` recompute, draw
```

It refuses a non-opaque value, because a replaced value is never mutated, and an object two
opaque fields hold, rather than guess.

### 2. Reads are tracked

A read records itself with whatever is currently consuming — a computed being evaluated, or
a render. The mechanism is the ContextVar the package already uses twice
(`runtime/resources.py:246`, and 40 §7).

```python
with sl.untracked():
    current = self.filters          # an action handler's read is not a dependency
```

`untracked()` is the one piece of ceremony this adds, and it is needed in exactly one
place: code that reads state to decide what to do, rather than to derive a value from it.
Action handlers run outside any consumer already, so in practice it is for a computed that
deliberately samples something without subscribing to it.

### 3. Computed: pull, versions, and one epoch

`depends=` is gone. A computed records the cells it read and the version each held. Asked
for its value, it walks that list; if no version has moved it returns the cache, otherwise
it recomputes with tracking on, rebuilding the list.

```python
@sl.computed
def either(self) -> str:
    return self.x if self.flag else self.y
```

While `flag` is `False`, `x` is not in the dependency set, so writing `x` recomputes
nothing. The set is what the last run actually read, so a conditional dependency is exact
rather than over-declared.

**Nothing is pushed.** A write bumps a version and calls `invalidate()` on the owner. It
does not walk a dependent list, because there is no dependent list — every reference points
from reader to source. That direction is the whole reason for this shape rather than a
conventional signal graph: components here are per-message and constantly destroyed, and a
source holding its readers keeps them alive. The spike measures it (`spikes/41/probe2.py`):
with back-references, a dropped reader stays alive; with reader-to-source only, it is
collected.

**One global write epoch makes repeated reads free.** Walking the source list on every read
costs, and a render reads a computed many times while writing none. A module-level counter
bumped by every write lets a node settled in the current epoch return immediately. Without
it the spike measured a three-deep chain at roughly four times the cost of the
alternatives; with it, all three fall inside this machine's run-to-run noise. That noise is
also why no phase of this plan claims a performance win: the models are chosen on
recompute counts and reference direction, both of which are deterministic.

**Laziness is the other half.** A computed nobody renders is never evaluated. Today
`_state_changed` refreshes every materialized computed on every commit
(`runtime/component.py:297-311`), so a value used in one branch of a render is recomputed
whether or not that branch is taken.

**Errors move.** A computed that raises now does so during render rather than during
commit. `refresh_for` (`:777`) currently swallows the exception and reports "changed",
which means a broken computed silently marks everything downstream dirty forever. Failing
where the value is used is the honest place.

#### 3a. Resources track the same way, with one presumption

`resource(depends=)` goes the same way `computed(depends=)` does: the loader runs with
tracking on, and reading `resource.state` re-pends it when a version it read has moved.
Pulled rather than pushed, for the same reason — the write that moved the input already
invalidated the owner, so the only thing left is for the next reader to notice.

Two things the loader case needs that the computed case does not:

- **A never-run loader presumes it reads everything the component declares.** `replace()`
  installs an authoritative value without running the loader, so there is no tracked set to
  compare against and a later write would go unnoticed. A resource therefore starts with
  every declared cell of its owner in its source set, and the first real run replaces the
  presumption with the truth. Over-subscribe, never under-subscribe.
- **A conditional read has to be hoisted where the branch is not the point.**
  `Lookup.results` consulted `self._request` only in its paging branch, so its first run
  never recorded it and paging then found nothing stale. The fix is one line and it is the
  honest one: the request selects the operation on every run, so it is read on every run.
  This is the cost of exactness, and it is paid once per loader, at migration.

### 4. Writes stage, and the transaction gets smaller

A cell write inside an action stages into the transaction's overlay; outside one it
commits immediately. `join_action` already returns `None` when no transaction is open
(`:399`), which is exactly that signal.

This is not new machinery — [40](40-shared-state.md) §5 specifies the overlay for shared
cells because a shared write crossing an `await` must not be a dirty read. Extending it to
component state is what lets a large amount of the current transaction go:

| Today | Under staging |
|---|---|
| `_Snapshot`, `_Transaction.record` (`:159-172`), `_restore` | The overlay holds the previous value. Rollback is dropping it. |
| `_plain` deep copy on first write per cell (`:61-68`) | A value that is never mutated makes a snapshot a reference. |
| `CopyMode` / `copy=` | Nothing copies, so there is no strategy to choose. `opaque=` carries what was left of the meaning. |
| `_Transaction.delta()` reading after-values from `__dict__` (`:203-226`) | The overlay knows both halves without touching `__dict__`, which is the `contribute()` seam 40 §5a already adds. |

Two details the phase settled that the sketch did not:

- **The cell outlives every value it holds.** A restore that puts a field back to unassigned
  empties the cell rather than dropping it, because a reader holds the cell object and a
  replacement would leave that reader watching something nothing writes to again.
- **Publication happens before `prepare`, not after.** A participant validates against the
  state the action actually left, which is what it did when writes went through. Nothing
  awaits between publication and the point of no return, so no other task can observe the
  window, and a failed prepare still rolls the whole action back from the overlay.

`born`/`protects`, `block_writes`, `readonly_transaction`, `strict_state` and
`report_undeclared_write` are unaffected and stay as they are.

### 5. Undeclared writes raise, and `strict_state()` goes

`Component.__setattr__` reports a transaction-time write to an attribute that is not
declared state, and `strict_state()` decides whether that report is a log line or an
`UndeclaredStateError`. The check stays and becomes the only behaviour; the flag goes.

The order inside `__setattr__` is what makes this a correctness change rather than a
volume change:

```python
if _CURRENT.get() is not None and name not in _FRAMEWORK_ATTRIBUTES and name not in type(self)._state_names:
    report_undeclared_write(self, name)      # raises
object.__setattr__(self, name, value)        # never reached
```

Under the warning path the write lands and is never rolled back, which is exactly the
damage the warning describes. Under the raise path it never lands and the transaction rolls
back whole. **The lax mode is the only one that produces the corruption it warns about.**

Three things make the removal cheap:

- **The check already fires almost nowhere.** `Component.__new__`
  (`runtime/component.py:283-288`) notes components born mid-action, so a handler that
  builds a component assigns to it freely, and so does every `__init__`. What is left is a
  write to a *pre-existing* component inside an action, which under §1's single rule is
  always a bug.
- **The suite already runs strict.** `tests/conftest.py` enables it autouse and nothing in
  `squid/` calls it, so the only in-tree behaviour that changes is
  `tests/test_transactions.py:71`, which exists to exercise the warning.
- **A raising handler is already handled.** It travels the ordinary `handle_error` path, so
  the reader sees an error rather than a silently stale panel. "It would crash production"
  overstates what happens.

`UndeclaredStateError` stays as the single outcome. `_STRICT`, `strict_state()` and its two
exports go.

### 6. One node type

40 §2 makes a shared namespace a `ReactiveOwner` so it can reuse the component-state
machinery. With cells staging and tracking their own reads, the two stop being similar
designs and become one:

```text
_Cell           value, version; staged through the transaction; read-tracked
  owned by a Component   -> a write calls owner.invalidate()
  owned by a Shared      -> a write publishes (owner, descriptor) on the bus
```

Everything else — replacement, tracking, staging, the delta — is the same code. The
consequences are worth naming:

- **A computed can depend on shared state.** `self.workspace.selected` read inside a
  `@sl.computed` records the shared cell as a source and recomputes when another panel
  writes it. Nothing supports that today, in either plan.
- **40 §4 resolves the other way.** It argued that a shared cell should behave like
  component state, and chose proxies because that is what component state did. The
  principle was right; with the proxies gone, parity means both require replacement. 41
  owns that rewrite. 40 is unimplemented, so this is doc churn and not migration.
- **40 §5b's guard is unchanged.** It compares values, and 41 gives every cell a version as
  well; the guard stays on values, because a version guard would reintroduce the A→B→A
  false positive 40 rejected on its own terms.

### 7. Migration

Small, because the counts in *Problem* say it is.

- **4 `depends=` sites** lose the argument: `patterns/source_ranked.py:84`,
  `patterns/browser.py:85`, `patterns/lookup.py:90`, `squid/bot/layout_showcase.py:144`.
  `lookup.py` also hoists its `self._request` read out of a branch; see §3a.
- **The two class-creation dependency checks go with it.** A computed may now read another
  component's state, so "dependencies must be fields on the same component" has nothing to
  enforce, and a cycle is reported where it runs rather than where it is declared.
- **In-place mutations become replacement.** `tests/test_mount.py` (148, 1304, 1894),
  `tests/test_durability.py:167`, and -- missed by the original count, which read only the
  package -- `squid/bot/settings_view.py:398`, which becomes a `{**m, k: v}` replacement.
- **14 `copy="ref"` sites** become `opaque=True`: 5 in `patterns/`, 5 in `squid/bot/`, 4 in
  the tests.
- **3 mutable state declarations** become tuples: `tests/test_mount.py` (132, 1297, 1885) and
  `tests/test_durability.py:25`. The original count of 0 was taken over `patterns/` and
  `squid/`, which are indeed clean; the tests are not.
- **2 strict-mode sites**: the autouse fixture in `tests/conftest.py` is deleted rather than
  inverted, and `tests/test_transactions.py` loses the warning-path case at 71 while keeping
  the raising cases at 66 and 93.
- **Nothing else.** No declared state anywhere uses a mutable default or factory, and
  nothing outside the tests calls `strict_state()`.

## Phases

| # | Deliverable | Exit criteria |
|---|---|---|
| 1 | `_Cell` with value and version; `state()` overloads narrowing `dict`/`list`/`set` to their read-only ABCs; `opaque=`; `mutated(obj)`; delete the proxy subsystem. **Shipped** (first with a runtime `hash()` check and `sl.FrozenMapping`, then amended to static enforcement). | A concrete `list`/`dict`/`set` annotation and a mutating method both type errors, pinned by a typing fixture; a `dict` default stored as-is and shared; `mutated(obj)` moving the holding field's version, refusing a non-opaque value and an object held twice; an `opaque=` field accepting a service; `ReactiveList`/`Dict`/`Set`/`_ReactiveMixin`/`_observe` gone. |
| 2 | Read tracking, `untracked()`; computed without `depends=`; versions, settle, the write epoch. **Shipped.** | A computed never stale; a conditional dependency recomputing nothing when the unread branch's input changes; a diamond recomputing the shared node once; a computed whose value settles unchanged not propagating; a computed nobody reads never evaluated; a dropped reader collected while its source lives. |
| 3 | Cells stage through the transaction; delete `_Snapshot`, `_plain`, `_restore`, `CopyMode`; undeclared writes always raise and `strict_state` is deleted. **Shipped.** | Rollback by dropping the overlay; read-your-writes; the delta built from the overlay; `export_state`/`restore_state` round-tripping without `_plain`; an undeclared write raising with no transaction flag set *and* leaving the attribute unwritten; a component built mid-action still assigning freely; `block_writes` and `readonly_transaction` unchanged. |
| 4 | Rewrite 40 §2, §4 and §5 onto `_Cell`. **Shipped, as doc work only:** 40 has no code yet, so unifying `sl.cell()` onto `_Cell` is 40's phase 1 to build, not this plan's to migrate. The runtime half that could be tested here — a computed recomputing when a *different owner* writes state it read — is covered in `tests/test_computed.py`. | 40 §2 and §4 describing one cell type rather than two similar ones; 40's bus publication and §5b value guard unchanged; 40's phase table naming what it still owns. |
| 5 | Docs, devtools cell/version inspection, migration of the call sites. **Shipped.** | The showcase and `patterns/` free of `depends=` and `copy=`; devtools showing a cell's version and a computed's current source set. Call-site migration landed with the phase that broke each one, since the tree does not run otherwise. |

## Verification

- `tests/test_reactivity.py`: a builtin container stored as assigned; `mutated(obj)`; `opaque=`;
  version bumps only on a real change; the equality short-circuit.
- `tests/typing_state.py`: the `state()` overloads, pinned with `assert_type` under
  `just typecheck`. Pyrefly does not report an unused ignore, so a negative pin would be silent.
- `tests/test_computed.py` (new): tracking without `depends=`; conditional dependencies;
  the diamond; the settle cut-off; laziness; `untracked()`; a raising computed failing at
  read rather than at commit; a dropped reader collected.
- `tests/test_transactions.py`: rollback by overlay drop; read-your-writes; the delta from
  the overlay; the phase-0 participant ordering unchanged; an undeclared write raising
  unconditionally and leaving the attribute absent; the autouse `strict_state` fixture in
  `conftest.py` removed rather than inverted.
- `tests/test_durability.py`: `export_state`/`restore_state` with no `_plain`.
- `spikes/41/compare.py` and `probe2.py` become the shape of the regression tests; the
  leak assertion in particular ports directly.
- `just typecheck` (compare against a pre-change run) and `git diff --check`.

## Consumers

The library user, per the productization standard. Unlike [40](40-shared-state.md) this
plan has in-tree consumers on day one — 18 call sites across `patterns/`, `squid/bot/` and
the tests — because it changes a primitive that already ships.

## Spike

`spikes/41/` holds the three prototypes this design was chosen from, the scenario harness,
and the probes. They are evidence, not a staging area: nothing there is meant to be
promoted into the package.

## Rejected alternatives

- **A read collector with eager refresh.** Prototype A in the spike: keep
  `_state_changed`'s recompute-compare-propagate shape and replace `depends=` with a
  ContextVar collector. It fixes both reproduced defects and it is by far the smallest
  diff; on read cost it is indistinguishable from the chosen design. It loses on work, and
  the counts are deterministic: it refreshes every materialized computed on every write
  whether or not anything reads it, and it recomputes a diamond's shared node twice where
  pull recomputes it once. It produced no glitch — the eager refresh settles level by level
  — so the objection is redundancy, not correctness. It remains the answer to fall back on
  if pull's machinery does not earn itself in phase 2.
- **A conventional signal graph with dependent lists.** Prototype B. Identical results to
  the chosen design on every scenario, and disqualified by one property the spike measured
  directly: a source cell holds its dependents, so a dropped reader stays alive. In a
  package where every component is per-message that is not a tuning problem. Weak
  references and unsubscription would fix it, and are precisely the complexity that
  pull-with-versions avoids by never needing the back-edge — which it can do only because
  invalidation here is whole-component and does not need pushing.
- **Deep proxies over arbitrary objects.** Vue's `reactive()`: recurse `_observe` through
  anything and intercept `__setattr__`. It would fix the deep-object hole with no call-site
  change at all. It breaks `isinstance`, identity, `is`, pickling, `slots=True` and frozen
  dataclasses; it fights the durability layer, which serializes state; and it is strictly
  more magic in a subsystem whose magic is the complaint. It also spends that to preserve
  a pattern with two call sites, both in tests.
- **Signals as explicit values** — `self.count = sl.signal(0)`, read `self.count()`, write
  `self.count.set(1)`. Exact tracking, and deep objects stop being the framework's problem.
  It costs the declared-type-is-the-real-type property `sl.state()` has today, grows
  parentheses on every read site in the package, and buys nothing over tracking inside
  `__get__`, which is an interception point that already exists. Signals are the
  implementation here, not the API.
- **A compiler or AST transform** (Svelte 5 runes, Vue Vapor). Nothing to compile: the
  descriptor protocol already intercepts reads and writes without rewriting source, and an
  import hook would cost debuggability and type-checker support for it.
- **Fine-grained invalidation.** The natural next step after a dependency graph, and it
  does not pay here. A Discord message is rendered whole, planned, diffed and sent as one
  edit, so knowing that exactly one node changed saves a render that costs microseconds
  against a round trip that costs tens of milliseconds. Whole-component invalidation stays.
- **A runtime immutability check via `hash()`.** What phase 1 first shipped. Deep and
  unconditional, and it earned neither: nothing in the machinery needs hashability, so the
  check was a lint, and the lint's price fell on the one container Python has no frozen
  literal for. Every mapping write site became
  `sl.FrozenMapping({**self._channels, setting: channel_id})`, and `FrozenMapping` spread
  into `forms.py` and three patterns to keep their return values assignable. The static
  overloads catch the same in-tree cases at the declaration instead of the write, and the
  wrapper, its module, its export and its tests are deleted.
- **A runtime annotation-based check.** [40](40-shared-state.md) carried one. It is shallow
  — `(1, [2])` and a frozen dataclass with a `list` field both pass it — and `__set_name__`
  receives no annotations, so reading them during class creation forces PEP 649 evaluation
  of names the module may not have defined yet. The *static* check has neither problem: the
  checker follows the types, so it is deep, and nothing is read at runtime.
- **Freezing at the boundary.** `__set__` converting a `dict` to a frozen mapping and a
  `list` to a tuple. It removes the wrapper from the write sites but replaces it with a
  value that silently changes type between assignment and read, in a subsystem whose magic
  was the complaint.
- **An `Immutable[T]` wrapper.** `state()` returning a handle with `.unwrap()`, so that a
  read has to go through the framework. As a pure typing trick it makes the value unusable —
  a union with an empty marker rejects every read, not only the writes — and as a runtime
  object it is the explicit-signals design above under another name. Counted: 597 reads and
  184 writes of state across the package and bot, against 46 declarations. The wrapper
  taxes the 597 to guard the 184, and `unwrap()` still has to be overloaded per builtin to
  return a read-only view, which is the same three overloads moved from the declaration to
  every read.
- **A `draft()` context manager.** `with sl.draft(self, "channels") as c:` — a shallow copy
  assigned back on exit. Built, then deleted: the only thing it saved over a local variable
  was the assignment line, and it paid for that with a string naming the field. Every typed
  spelling — a selector lambda resolved against a recording proxy, a descriptor handle — is
  longer than the three-line idiom it wraps.
- **Keeping `depends=` as an optional override.** A second way to express the same fact,
  which can disagree with the first. The tracked set is what the code actually read; a
  declared set that differs from it is either redundant or wrong.
- **`__slots__` on components.** The structural version of §5: generate slots from the
  declared cells so an undeclared attribute is a native `AttributeError` — no runtime check,
  no ContextVar read on the write path, and enforced outside transactions too. Counted and
  rejected: 174 undeclared `self.X = ...` assignments across the `__init__` methods of 66
  `Component` subclasses are collaborators, callbacks and config rather than state, and the
  framework reads or writes `__dict__` in 16 places across 7 modules. Slots would need a
  declaration syntax invented for the first group and a redesign for the second, to catch a
  class of bug the always-raising check already catches.
- **Keeping `strict_state()` with its default flipped.** An opt-out for an operator facing a
  crash loop without shipping a patch. Rejected because of what is being opted out of: the
  write does not land either way, so disabling the error does not restore working behaviour,
  only silent behaviour. A host that wants a failed action handled gently has
  `ActionMiddleware` and `handle_error`, which act on the failure without resurrecting the
  write.
- **Keeping the proxies behind an opt-in.** 183 lines and a second value model retained for
  two test call sites. If a real need appears, a mutable container is a value the author
  owns and replaces, or `opaque=True`.
