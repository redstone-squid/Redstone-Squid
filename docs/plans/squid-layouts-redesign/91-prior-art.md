# 91 — Prior art

An external survey (2026-08-24) placed the `state`/`computed`/`resource`/`action`/`operation`
model against eleven existing systems: Dioxus, Jetpack Compose snapshots, Salsa, Clojure STM,
Missionary/Electric, Jane Street Incremental, Glimmer, Svelte 5, Jotai, TanStack DB, and the
Rust TUI reactive crates. Its conclusion — that the model is a junction of four mature lineages
rather than one invention — is correct and worth having written down.

This file is the check of that survey against the tree, because a survey written from a README
cannot tell which of its recommendations are already shipped. The result: **most of it is
confirmation, four of its claims are wrong about what is here, and three things are genuinely
absent** — one of which is a latent scaling problem worth naming before it is felt.

Same discipline as [90](90-deferred.md): the reasoning is recorded so it is not re-derived, and
so the next survey can be answered by pointing at a file. §§1–5 answer the first survey. A second
arrived the same day having read that answer; §6 handles it, and it is where the three proposals
neither round had originally listed live — commutative updates, long-lived forks, and retryable
pure actions.

## 1. Already shipped

Each row is the mechanism the survey named and the thing in the tree that is it. `core.py`,
`resources.py` and `operations.py` are under `packages/squid-reactive/src/squid_reactive/`.

| Survey's finding | In the tree | Evidence |
|---|---|---|
| Compose `MutableSnapshot`: isolated reads, snapshot-local writes, atomic `apply()` | The staged overlay: writes land in `_Transaction.writes`, reads see them, `publish()` installs them under one lock | `core.py:767`, `core.py:786`, `core.py:1006` |
| Compose `SnapshotApplyConflictException` | `ReactiveConflictError`, raised when a strong input moved | `core.py:161`, `core.py:729` |
| Compose read observers discovering UI dependencies | `Observation` — the same tracked read a computed records, in render context | `core.py:1710`, `core.py:1779` |
| Compose: `withMutableSnapshot` may not suspend | Aftermath hooks are synchronous by rule; an awaitable is closed and rejected | `core.py:1032` |
| Clojure `ensure` (read-set validation) | `strong_read()`, plus automatic compare-and-set on any cell the action read *and* wrote | `core.py:438`, `core.py:743` |
| Clojure's "avoid I/O in transactions, it retries" | Same trap, same answer: effects belong in an `operation` the action arms | `operations.py:213`, [90's `action.status` entry](90-deferred.md) |
| Clojure agents: dispatches held until commit, discarded on abort | `on_action_commit` / `on_action_rollback` / `on_action_outcome`, failure-isolated | `core.py:1198`–`core.py:1210` |
| Salsa revisions: per-input change versions plus a database-wide revision | Per-cell `version` plus the global `_EPOCH`; a node settled in the current epoch skips walking its sources | `core.py:394`, `core.py:1636` |
| Salsa backdating: a recompute producing an equal value does not propagate | `_Derived.settle` bumps its version only when the new value differs | `core.py:1656` |
| Salsa dynamic tracked dependencies, no declared lists | `_CONSUMER` records what the last run read, so a conditional dependency is exact | `core.py:1646`, `computed` at `core.py:1699` |
| Glimmer autotracking and `@cached` getters | `computed` — the same thing, arrived at independently and already credited to the same lineage in [41](../completed/squid-layouts-redesign/41-reactivity-cells.md) | `core.py:1699` |
| Dioxus: signal reads tracked *through* `await` inside a resource | `_CONSUMER` is a `ContextVar` set around the loader await, so reads before and after an `await` both land in that generation's read set | `resources.py:533`–`resources.py:541` |
| Dioxus: invalidation cancels the old future | `_new_generation` cancels the superseded load's scope | `resources.py:578`, seam at `resources.py:122` |
| Jane Street Incremental: demand-driven, only observed nodes are necessary | Pull model throughout — a computed nobody reads is never evaluated, and no source holds a back-edge to its readers | `core.py:1614` docstring, [32](32-demand-driven.md) |
| Missionary: structured cancellation and an ownership hierarchy | Deliberately *not* in `squid-reactive` (`dependencies = []`); the task-group hierarchy is `squid-layouts`' mount lifetime | `resources.py:78` |
| TanStack DB: optimistic overlay distinct from the remote write | `Resource.replace` stages through a participant; the remote half is an `operation` | `resources.py:434`, plan 48 |
| TanStack DB: explicit transaction lifecycle states | `Pending`/`Ready`/`Failed` for resources, `Pending`/`Succeeded`/`Failed`/`Cancelled` for operations | `resources.py:145`, `operations.py:48` |
| Compose merge policies for commutative writes (counter, set union) | `ReplicatedCounter.increment` and `ReplicatedSet.add`/`discard`, staged into the same action | `squid-replicated/document.py:362`, `:376` |
| Missionary glitch freedom (`y = x + x` never observes a torn `x`) | Free by construction: reads are pull, and one action's writes install under `_COMMIT_GATE` before any consumer is notified | `core.py:786`, `core.py:902` |

The one item worth stating positively rather than as a table row: **glitch freedom is not
something this design has to work for.** Push-based FRP has to schedule a topological pass to
avoid a half-updated graph; a pull graph with an atomic publish step cannot produce one, because
nothing recomputes until someone reads, and by then everything is installed. Missionary's central
guarantee is this model's default. It is currently a property of the code rather than of a test,
which is the one thing worth fixing here — see §4.

## 2. Where the survey is wrong about this tree

Four claims that do not survive contact. They are recorded because each one, taken at face
value, points at a change that would be a regression.

**"Squid allowing transactions across `await` is pushing into harder territory than Compose."**
Half true, and the false half is the interesting one. A transaction here does span an `await` —
but it is confined to the task that opened it. A sibling task under one `gather` that inherited
the `ContextVar` copy cannot stage into it, and a task that outlives the handler cannot either;
both raise `StaleReactiveContextError`, naming which of the two mistakes it was (`core.py:147`,
`core.py:679`, `core.py:687`). So the hard case the survey imagined — concurrent writers
interleaving into one overlay, where what commits depends on scheduling — is refused rather than
solved. That is a *narrower* position than Compose's, not a bolder one.

**"Dioxus's rule — resource computations may be dropped at any `await` — is probably exactly the
rule Squid should adopt."** No. The shipped contract is deliberately weaker and should stay
weaker: a loader is safe to run zero, one, or many times, and its result is discarded if
superseded (`resources.py:129`). Cancellation is an *optional* host-installed seam on top of
that, because `squid-reactive` is dependency-free and anyio is where CLAUDE.md puts cancellation.
Promoting "may be dropped at any await" to the rule would retroactively make every loader that
is not cancel-safe illegal, in exchange for nothing — the capability is already available to a
host that wants it. Adopt Dioxus's mechanism, which is done; reject its contract.

**"Squid state could eventually have `merge=`, which would be far better than baking one
concurrency policy into every state cell."** The premise is false: there is no single baked
policy. A blind write is last-commit-wins, a cell the action read *and* wrote is compare-and-set
automatically, and a read the action merely branched on is opted into validation with
`strong_read()` (`core.py:743`). That is three policies selected by what the action actually did,
and the choice between them was made twice, in both directions — see 90's "serializable actions
by default" entry. Commutative merge, the case `merge=` exists for, is `squid-replicated`. See
§3.2 for whether the residue is worth anything.

**"Jotai shows another way around JS's missing AsyncContext."** True and irrelevant. Python has
`ContextVar` and it already carries the consumer across the loader's awaits. The three-design
comparison (ambient / AsyncContext / explicit `get`) is a note for a hypothetical JS port and
should not be read as an open question here.

One more, less a correction than a sharpening. The survey treats Salsa's backdating and this
package's version lineage as the same idea. They are two, applied at different layers on purpose:
a **computed** suppresses on equality (`core.py:1656`), while a **cell** moves its version on
every write even back to a value it previously held, so A→B→A conflicts. Plan 68 chose that split
explicitly and 90 records it as worth keeping. A future reader tempted to "unify" them would be
undoing a decision, not removing a duplication.

## 3. Genuinely absent

Three things. Only the first is worth acting on, and not yet.

### 3.1 Salsa's durability tiers — the global-epoch fast path, named

`_EPOCH` is a single process-wide integer bumped by every write anywhere (`core.py:394`,
`core.py:402`). A derived node that settled in the current epoch returns its version without
walking its sources; once *any* write lands, that fast path is gone for **every** node in the
process, and each one re-walks its whole source dict on its next read (`core.py:1636`).

That is fine at current scale and is a real cliff at a larger one: cost per write-burst is
O(live derived nodes × their source counts), and it does not matter whether the write had
anything to do with those nodes. A bot with many live mounts, a topic bus publishing on every
Discord event, and computeds two chains deep is the shape that finds it.

Salsa's answer is durability: inputs expected to change rarely are marked high-durability, and a
computation depending only on those can skip traversal entirely when only low-durability inputs
moved — per-tier epochs instead of one. In this tree, guild settings and locale are
high-durability; a paginating cursor is not.

**Decision: file, do not build.** The cliff is real but unmeasured, and the fix would change the
hottest path in the package. The removal condition is a measurement, not a consumer: profile a
settle pass under many live mounts — [37](37-runtime-profiling.md)'s counters, reachable through
`sl.discord.devtools`' `ui metrics`, already carry the data — and act if source-walking shows up.
Recorded now so that when it does show up, the answer is not re-derived from first principles.

### 3.2 `merge=` on plain in-process `state()`

The residue after §2's correction is narrow: a cell that two *concurrent in-process actions* both
write, whose writes commute, and which is not worth a replicated document. Rejected as an
addition, on three grounds.

A merge function would have to run at commit time, inside `_COMMIT_GATE`, between
`check_preconditions` and `publish`. That is author code in the one region the participant split
exists to keep author code out of — everything fallible happens in `prepare` (`core.py:87`). A
merge that raises would have to become a conflict, which is where we started. (**Weakened by
[§6.3 A](#a-commute-beats-a-three-way-merge--and-it-sharpens-32-rather-than-overturning-it)**: that
region is inside `_COMMIT_GATE` but *before* `publish`, so a raising merge really could just be a
conflict. The two grounds below are the load-bearing ones.)

`ReplicatedCounter`/`ReplicatedSet` already answer the commutative cases with real CRDT
semantics, an encodable inverse for history, and a backend chosen on measured evidence
([the backend ADR](../68-replicated-backend-adr.md)). A hand-written `commutative_add` would be a
second, weaker spelling of a shipped one.

And the motivating concurrency is thin here. Compose merges snapshots because composition and
input run concurrently by construction; actions in this runtime are serialized by dispatch, and
where they are not, `strong_read()` is the stated answer. As 90 notes, **on the web the default
probably does invert** — that is where this entry gets reopened, and if it is, the question to
ask first is whether merge belongs on the cell or on the participant.

### 3.3 Differential dataflow over collections

TanStack DB maintains live query results incrementally: one record changing updates the affected
part of a filter/join/aggregate rather than recomputing the query. Nothing here does that — a
computed over a collection recomputes wholesale, which is correct and, at the collection sizes
this bot renders (a page of builds, a role list), cheaper than maintaining a difference.

**Deferred**, with an explicit condition: a computed whose input collection is large enough that
recomputation shows up in a settle-pass profile, *and* whose consumers need only the delta.
Pagination already keeps most candidates below that line. If it arrives, differential dataflow is
a layer above signals, not a change to them.

### 3.4 Nested transactions — rejected

Compose nests mutable snapshots; `transaction()` flattens (`core.py:1077`). Not a gap. A nested
transaction that could commit independently of its parent would be a second commit point inside
one action, and the single case that genuinely needed a distinct causal action already has an
answer that is honest about the tradeoff: `fresh_action_transaction` suspends the outer envelope
and refuses outright if it has staged anything (`core.py:1133`). Nesting would replace an
explicit refusal with a semantics question at every `with`.

## 4. What to do

| Action | Where |
|---|---|
| **Do:** add a glitch-freedom test — two computeds over one cell, asserted consistent across a commit that moves it, and across a rollback | `packages/squid-reactive/tests/` |
| **Do:** fix 90's stale entry — "abandoning a superseded resource load" shipped in `34d56b52`, exactly as that entry predicted (seam in `squid-reactive`, cancellation installed by `sl.discord`) | [90-deferred.md](90-deferred.md) |
| **File:** durability tiers, with the measurement as the removal condition | §3.1 |
| **File:** differential dataflow over collections, with the condition | §3.3 |
| **Reject:** `merge=` on `state()`, nested transactions, and "dropped at any await" as a contract | §2, §3.2, §3.4 |
| **No action:** everything in §1 | — |

Nothing in this survey changes a shipped design. That is the useful result: eleven systems, and
the only two items that produce work are a missing test and a stale documentation line.

## 5. If you read the sources

The survey's suggested reading order is good and worth keeping, with one change — read Compose
before Dioxus, because the transaction model is where the harder unsolved questions are while the
resource model is the one already answered here.

1. **Compose `Snapshot` / `SnapshotMutationPolicy`** — the closest thing to the action
   transaction, and the only system in the list that took merge policies seriously.
2. **Clojure refs, `ensure`, `commute`, agents** — fifteen years of production experience with
   exactly the boundary `strong_read()` and `on_action_commit` sit on.
3. **Salsa's algorithm page** — durability is §3.1's answer, already written down.
4. **Dioxus resources** — confirms the mechanism; do not adopt the contract (§2).
5. **Missionary** — for the structured-concurrency and dynamic-DAG framing, not for anything to
   copy; the cancellation half is `squid-layouts`' by design.

## 6. The second survey

A follow-up (2026-08-24, written after reading §§1–5) reaches the same headline conclusion —
*stop adding primitives; the best research tool now is a hostile workload, not another
abstraction* — and adds three proposals that neither round had listed. Its own summary sorts
everything into "core is mature / plausible extension: commutative updates / future optional
layers / do not add without profiling / probably redundant", and that sort is right except for
one row.

### 6.1 Confirmed, no action

**Salsa durability: wait for profiling.** Agrees with §3.1, with numbers — under 5% likely to be
needed for Discord-sized UIs, 15–25% for a general web runtime. Its added suggestion, "design the
internals so this is possible later, expose nothing publicly", needs no work: durability is a
per-tier epoch array replacing one global integer, entirely inside `_bump_epoch` and
`_Derived.settle`, with no public surface to prepare and no field to add now. Speculative internal
metadata (`cell.change_class`) would be the thing that goes stale. Recorded so nobody builds
scaffolding for it.

**Differential dataflow belongs in a separate layer implementing the common source protocol, and
ordinary `state[list[T]]` must not learn collection-query semantics.** Agrees with §3.3, and the
shape it draws is already available: the "common `ReactiveSource` interface" it proposes exists de
facto, since `_Cell`, `_Derived` and `Resource` all answer `settle() -> int`, carry `sources`, and
are walked structurally by `Observation.addresses()` (`core.py:1756`). A `TableView` would join by
implementing those two members. So this needs no preparatory seam either. The "do not teach
`state()` about collections" half is also the standing position, for the same reason [41
](../completed/squid-layouts-redesign/41-reactivity-cells.md) rejected deep proxies over `dict`/`list`/`set`.

**CRDT/local-first as a third tier — `State` / `Shared` / `Replicated`, each implementing one
observation/version interface, "a remarkably clean extension point".** Shipped, and it is the
package layout: `state()` is local and OCC, `Shared[ScopeT]` is addressed and published on the
bus, and `squid-replicated` joins the same action through the `ActionParticipant` seam. Plans 40,
45, 47, 55, 63 and 68.

The Automerge argument behind it — that replacing a whole immutable object destroys the intent
needed to merge concurrent edits to *different fields*, turning compatible changes into one
conflict — is also already the reason `squid-replicated` exposes `ReplicatedCounter.increment`
and `ReplicatedSet.add`/`discard` rather than replicated scalar cells. Those stage
`engine.operation("increment", path, amount)`: an operation, not a value with a merger attached
(`squid-replicated/document.py:362`, `:376`).

### 6.2 Corrections

**`ensure`: rated "5% worth adding". It is `strong_read()`, it shipped, and it is load-bearing.**
The reasoning offered is that Squid's default isolation sounds *stronger* than Clojure's ordinary
`ref` semantics — every `ObservedRead` validated at commit — so an explicit `ensure` would mean
`foo # already ensured`. That premise was true for about three days. Plan 68 shipped exactly that
strict default and it was **narrowed back on 2026-08-24** (`85b633e5`, "make serializable actions
opt-in"): a read-only read now carries no precondition unless taken inside `strong_read()`,
because the common handler here reads a namespace for a permission check and writes something
unrelated, and full validation aborts actions that would have succeeded harmlessly
(`core.py:729`, and 90's "serializable actions by default" entry for the full argument).

So the default is *weaker* than the survey assumes, `strong_read()` is `ensure` under another
name, and removing it would remove the only way to express a read-A-write-B invariant. The
inverse API the survey predicts would be the interesting one — "give me the value, but don't add
it to the validation set" — also exists, as `relaxed_read()` (`core.py:423`), with precisely the
narrower job it guesses at: opting one read back out from inside a `strong_read()`. Both halves
are shipped; what is striking is that both were derived from the strict default, and only one of
them survived inverting it.

**Durable aftermath / transactional outbox: "for a Discord layout library, probably unnecessary".
Built, and in production shape — for one direction.** `CompensationOutbox` /
`TransactionalCompensationOutbox` carry idempotency keys, a claim/dispatch/reconcile lifecycle,
bounded records, retry limits and restart recovery (`squid-layouts/runtime/histories.py:181`
onward). The load-bearing part is `participant()`, which stages first-seen intent persistence *in
the same commit as the action record* — the transactional outbox at the correct gate, not a
best-effort write after it. Plan 68 built it after finding the consumer 90 had been waiting for.

The gap is real but smaller and more specific than the survey frames it. What exists covers the
**backward** direction: an action whose external effect already happened and whose commit then
failed. The **forward** direction the survey describes — commit succeeds, process dies, the
aftermath never runs — is not covered, and the reason is one field. `CompensationIntent` carries
*identity* (operation context, original action id, idempotency key, timestamp) and no payload; the
effect itself is the author-supplied inverse held in a live in-memory history entry
(`histories.py:152`). A durable forward intent needs a **serializable** payload and a worker that
can execute it with no live history to consult. The discipline is proven and reusable; the
encoding half is absent. Not worth building for a Discord bot — recorded so that a web port knows
it is inheriting three-quarters of the machinery.

### 6.3 Genuinely new

#### A. `commute` beats a three-way merge — and it sharpens §3.2 rather than overturning it

The strongest contribution in either survey. The argument: a cell stages *the value the author
computed*, not the operation they intended, so Compose's `merge(base, current, applied)` cannot
recover `+1` from `base=10, proposed=11` for anything but a type whose delta is inferable.
Clojure's split is cleaner — a normal write conflicts when its version moved, while a `commute`
records the operation and is allowed to be re-applied against the newer committed value.

That is correct, and it is the reason `merge=` on `state()` is the wrong *shape* — independent of
the three grounds §3.2 gave. One of those grounds should also be weakened in the light of it:
§3.2's claim that a merge function would be "author code in the region the participant split
exists to keep author code out of" is overstated. A merge or a re-applied operation would run in
`check_preconditions`, *before* `publish` (`core.py:859`, `core.py:886`) — inside `_COMMIT_GATE`
but not past the point of no return, so one that raised could simply become the conflict it
already is. That ground is weak; the other two stand.

And the decisive evidence is one package over: **`squid-replicated` already chose operation
semantics over value merging**, for exactly this reason. So the survey's preferred design is the
shipped one, and the only open question is whether a *local, non-replicated* cell deserves the
same treatment — which is precisely the residue §3.2 called thin.

Position unchanged, better argued and with a sharper trigger. `merge=` stays rejected; `commute`
is plausible later; **the trigger is evidence, not design**. The survey names the right evidence:
look at real `ReactiveConflictError` contention and see whether it divides cleanly into "this
genuinely is a conflict" and "these two operations algebraically commute". That is measurable
today — `ActionLedger` records a `ConflictDetail` on every conflict rollback (`actions.py:473`,
`core.py:1063`) — so the removal condition is a ledger sample from a contended workload, not an
intuition. If it fires, the first question is whether commute belongs on the cell or on the
participant, since the participant already hosts the replicated answer.

#### B. Long-lived forks — the machinery exists, the consumers do not need it yet

Not on either absent list, and the most interesting idea in either survey. Its central
distinction is right and worth keeping in writing: **a fork is not a long transaction.** A
transaction here is an ambient dynamic context confined to the task that opened it, and holding
one open across minutes of user interaction is exactly the mistake `StaleReactiveContextError`
exists to catch (`core.py:147`). A fork would be a detached delta with a base version, merged
later — no ambient context at all.

But every consumer it names — editing forms, settings dialogs, previews, optimistic UI — is
already served one layer up, by staged-versus-committed values in the pattern library:
`MultiChoiceState(staged, committed)` (`patterns/multichoice.py:52`), `EditorState`'s per-section
`committed` slot (`patterns/editor.py:35`), the shared `CommitPolicy` (`patterns/commit.py`), and
`Resource.replace` for the optimistic case. Those three do not need base-version conflict
detection, because a staged value there never races anything: it is component state, private to
one mount, and the *only* writer is the person looking at it.

So the honest finding is that **a fork generalizes machinery that already exists, for consumers
that do not currently need the generalization** — and by the survey's own bar (the same awkward
shape five to ten times), three implementations with three different shapes is not yet a
primitive. It becomes interesting at one specific moment: when a draft must outlive its mount or
its process. Then it *does* race, and at that point it is `Shared` plus a persisted pool plus
per-cell merge — which means forks want (A) first. **The order is evidence → commute → forks**,
and building forks first would produce a branch that can only ever be abandoned or blindly
applied.

#### C. Retryable pure actions — rejected here, genuinely open for a web port

The proposal: given OCC validation, `ReactiveConflictError`, and irreversible work already pushed
out into operations and aftermath, a `@pure_action` could carry a contract (may read, write,
compute and schedule aftermath; may not perform irreversible effects) that makes automatic
conflict retry valid, as Clojure's STM does. Its own three-tier sketch — conservative `Action`,
transactionally-pure `RetryableAction`, effectful `Operation` — is a clean separation.

The ingredients really are present, including re-entry precedent: plan 64's challenged admission
already re-enters `dispatch` from the top after an approval, deliberately, so that access lost
while the dialog was open still refuses the press.

What kills it here is the frontend, which neither survey can see. An action is not a re-runnable
thunk; it is a handler invoked inside a dispatch holding a **single-use Discord interaction token
with a three-second acknowledgement deadline**. A retry must happen either before acknowledging —
spending a hard deadline on speculative work that may conflict again — or after, by which point
the token is consumed and the user has been told an action succeeded that is being re-run.
Clojure retries a pure function with nobody watching; this would retry something a person is
looking at, on a clock.

Second, the contract is unenforceable in Python. The nearest existing thing, `block_writes`
(`core.py:1273`), restricts *state* writes and knows nothing about I/O, and Clojure's own `io!` is
a marker an author must remember to use. `@pure_action` would be prose with a decorator on it.

**Rejected for the Discord runtime; recorded as a porting note** — on the web, actions are HTTP
handlers, retry-before-responding is ordinary, and there is no consumed token. Same category as
90's "on the web the default probably does invert", and the two are related: a runtime where
concurrent actions are ordinary is the one that wants both serializable-by-default *and* retry.

### 6.4 The process recommendation, and what already implements it

"Stop adding primitives for a while; build hostile workloads and see where the five become
awkward; if the same awkward pattern appears five to ten times, then it deserves a primitive."
Agreed, and worth recording that the instrumentation for it is already built rather than being
the next thing to design. `_checkpoint` places named deterministic pause points through the whole
commit path — `commit.before_validation`, `transaction.close_staging`,
`commit.after_participant_prepare`, `aftermath.before_hook` (`core.py:203`) — and
`InterleavingHarness` (`squid_reactive.testing`) drives them, so an adversarial schedule is
written as `schedule.at("commit.before_validation", ...)`. `packages/squid-reactive/tests/
test_interleaving.py` already reproduces write skew and A→B→A lineage movement on demand.

What is missing is not a harness. It is volume, and a workload contended enough to produce the
conflict sample (A) needs.

### 6.5 What to do

| Action | Where |
|---|---|
| **Do:** fix `MemoryCompensationOutbox`'s docstring — it cross-references a `restore` method that does not exist; the restart path is the `records=` constructor argument, as its own test uses (`packages/squid-layouts/tests/test_history.py:507`) | `histories.py:201` |
| **File:** `commute`, with a ledger sample from a contended workload as the removal condition — not a design exercise | §6.3 A |
| **File:** long-lived forks, ordered *after* commute, with "a draft that outlives its mount or its process" as the trigger | §6.3 B |
| **Porting note:** retryable pure actions, and durable *forward* aftermath intents — both inherit most of their machinery, both are blocked here by Discord facts rather than by design | §6.2, §6.3 C |
| **No action:** durability scaffolding, a `ReactiveSource` seam for differential dataflow, `ensure`, a CRDT tier | §6.1, §6.2 |

Round two produces one line of work, the same as round one. The useful output of both is the
recorded reasoning, and one sharpened position: **`merge=` is rejected on shape, `commute` waits
on evidence that is already being collected.**
