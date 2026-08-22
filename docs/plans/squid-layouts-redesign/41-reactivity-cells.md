# 41 — Reactivity: pull-based cells, tracked reads, immutable values

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

> A cell holds an immutable value and a version. A read is tracked. A computed recomputes
> when a version it read has moved. Nothing is pushed.

### 1. Values are immutable, and the check is `hash()`

```python
class Panel(sl.Component):
    filters: Filters = sl.state(Filters())        # frozen dataclass of immutable fields
    rows: tuple[str, ...] = sl.state(())

panel.rows = [1, 2]          # MutableStateError
panel.rows = (1, [2])        # MutableStateError -- an annotation check cannot see this
panel.filters = replace(panel.filters, limit=25)
```

Hashability is the test, applied at write. It is not a perfect immutability oracle — a
plain mutable object hashes by identity — but it is **deep**, which is the property that
matters and the property the annotation check [40](40-shared-state.md) used to carry did
not have. `(1, [2])` and a frozen dataclass with a `list` field both fail, and those are
the cases that actually bite. One `hash()` per write, on values that are almost always
already hashed elsewhere.

**`opaque=True` replaces `copy="ref"`.** A service, a guild, a `_WindowRequest` — a
collaborator the component holds and does not mutate — is declared for what it is:

```python
_request: _WindowRequest = sl.state(_WindowRequest(), persist=False, opaque=True)
```

The check is skipped for it and the promise sits at the declaration where a reader will
see it. The concept survives the rename because it is now the only escape hatch there is;
what it stops meaning is a copying strategy, since no snapshot copies anything any more.

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
| `_plain` deep copy on first write per cell (`:61-68`) | Immutable values make a snapshot a reference. |
| `CopyMode` / `copy=` | Nothing copies, so there is no strategy to choose. `opaque=` carries what was left of the meaning. |
| `_Transaction.delta()` reading after-values from `__dict__` (`:203-226`) | The overlay knows both halves without touching `__dict__`, which is the `contribute()` seam 40 §5a already adds. |

`born`/`protects`, `block_writes`, `readonly_transaction`, `strict_state` and
`report_undeclared_write` are unaffected and stay as they are.

### 5. One node type

40 §2 makes a shared namespace a `ReactiveOwner` so it can reuse the component-state
machinery. With cells staging and tracking their own reads, the two stop being similar
designs and become one:

```text
_Cell           value, version; staged through the transaction; read-tracked
  owned by a Component   -> a write calls owner.invalidate()
  owned by a Shared      -> a write publishes (owner, descriptor) on the bus
```

Everything else — immutability, tracking, staging, the delta — is the same code. The
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

### 6. Migration

Small, because §1's counts say it is.

- **4 `depends=` sites** lose the argument: `patterns/source_ranked.py:84`,
  `patterns/browser.py:85`, `patterns/lookup.py:90`, `squid/bot/layout_showcase.py:144`.
- **2 in-place mutations**, both in `tests/test_mount.py` (148, 1304), become replacement.
- **12 `copy="ref"` sites** become `opaque=True`.
- **Nothing else.** No declared state anywhere uses a mutable default or factory.

## Phases

| # | Deliverable | Exit criteria |
|---|---|---|
| 1 | `_Cell` with value and version; immutability check; `opaque=`; delete the proxy subsystem. | `MutableStateError` on a list, on `(1, [2])`, on a frozen dataclass with a list field; an `opaque=` field accepting a service; `ReactiveList`/`Dict`/`Set`/`_ReactiveMixin`/`_observe` gone and the two test call sites migrated. |
| 2 | Read tracking, `untracked()`; computed without `depends=`; versions, settle, the write epoch. | A computed never stale; a conditional dependency recomputing nothing when the unread branch's input changes; a diamond recomputing the shared node once; a computed whose value settles unchanged not propagating; a computed nobody reads never evaluated; a dropped reader collected while its source lives. |
| 3 | Cells stage through the transaction; delete `_Snapshot`, `_plain`, `_restore`, `CopyMode`. | Rollback by dropping the overlay; read-your-writes; the delta built from the overlay; `export_state`/`restore_state` round-tripping without `_plain`; `block_writes`, `readonly_transaction` and `strict_state` unchanged. |
| 4 | Unify 40's `sl.cell()` onto `_Cell`; rewrite 40 §2, §4 and §5 to match. | A `@sl.computed` depending on a shared cell recomputing when another owner writes it; 40's bus publication and value guard unchanged; one action writing component and shared cells committing once. |
| 5 | Docs, devtools cell/version inspection, migration of the 18 call sites. | The showcase and `patterns/` free of `depends=` and `copy=`; devtools showing a cell's version and a computed's current source set. |

## Verification

- `tests/test_reactivity.py`: the immutability check across the three shapes; `opaque=`;
  version bumps only on a real change; the equality short-circuit.
- `tests/test_computed.py` (new): tracking without `depends=`; conditional dependencies;
  the diamond; the settle cut-off; laziness; `untracked()`; a raising computed failing at
  read rather than at commit; a dropped reader collected.
- `tests/test_transactions.py`: rollback by overlay drop; read-your-writes; the delta from
  the overlay; the phase-0 participant ordering unchanged.
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
- **An annotation-based immutability check.** [40](40-shared-state.md) carried one and this
  plan does not revive it. It is shallow where `hash()` is deep — `(1, [2])` and a frozen
  dataclass with a `list` field both pass it — and `__set_name__` receives no annotations,
  so reading them during class creation forces PEP 649 evaluation of names the module may
  not have defined yet, in a package that bans quoted forward references to rely on that
  laziness.
- **Keeping `depends=` as an optional override.** A second way to express the same fact,
  which can disagree with the first. The tracked set is what the code actually read; a
  declared set that differs from it is either redundant or wrong.
- **Keeping the proxies behind an opt-in.** 183 lines and a second value model retained for
  two test call sites. If a real need appears, a mutable container is a value the author
  owns and replaces, or `opaque=True`.
