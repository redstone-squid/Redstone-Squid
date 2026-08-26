# Plan 68 replicated backend research report

Status: implemented; production adapter promoted 2026-08-27
Date: 2026-08-24
Repository evidence: `03a7e095` and `cf650fa1`
Evaluated releases: Loro Python 1.13.2, pycrdt 0.14.2, and an isolated pycrdt 0.14.4 verification

## Decision

Use **Loro as the production-hardening candidate for Plan 68's generalized `Replicated` backend**.
Do not promote the current experimental `LoroTextEngine` as-is. Keep pycrdt as a conformance oracle and
an explicitly text/array/map-only fallback, not as the general backend behind the planned public type set.

This resolves the dependency direction without lowering the production gate:

- Loro matches the required document model: text, list, movable list, map, tree, and counter live in one
  document and one before/after-frontier token can group changes across those containers.
- Loro can truncate history at the oldest retained token's `before` frontier. Tokens at or after that
  boundary continue to work, while older tokens have an inspectable shallow-history boundary and can be
  classified as expired.
- pycrdt is faster in the focused text measurements and has a cleaner Python error model, but its native
  public types stop at text, array, map, and XML. It has no native replicated counter, set, movable list, or
  general tree corresponding to Plan 68's intended abstraction.
- pycrdt action tokens depend on deleted Yrs items. With GC enabled, an executed test observed
  `UndoManager.undo()` return `True` while a deleted character was not restored. The safe experimental
  adapter therefore uses `skip_gc=True`, making compaction an all-history coordination event rather than a
  selective retained-token boundary.

At research time, the generalized production adapter was blocked on type-aware inverse planning, not on text
CRDT mathematics. A raw Loro `diff(after, before)` correctly preserves later text insertions and counter
increments, but it restored an old LWW-map value over a causally later remote value. Map/register paths must
therefore use an action-token precondition and return a typed conflict when their winning operation changed.
No production code may use raw document-wide frontier reversal as a universally safe inverse.

## Implementation outcome

The production adapter now satisfies the hardening list below. `LoroBackend` is explicitly injected into a
`Replica`; the public document exposes immutable named handles for all six Loro classes plus Squid's tagged
set. Exact counters use per-peer decimal totals rather than Loro's floating-point counter. Text alone uses a
frontier reverse diff, filtered to the action's affected text roots. Lists, movable lists, maps, trees,
counters, and sets stage semantic inverses; every replacement or move-like path first verifies its recorded
action authority, and one mismatch conflicts the entire action before staging.

History entries automatically acquire and release token leases. Shallow compaction intersects the retained
before-frontiers, checkpoints preserve that boundary across reload, and unleased tokens behind it return a
typed expired conflict without entering Loro's missing-history failure path. Binding failures whose exact
type is the built-in `BaseException` are translated without catching cancellation or process-control
exceptions. Update/token size, operation count, root count, path size, and container cardinality are bounded.

The versioned representative workload is `benchmarks/fixtures/loro_document_v1.json`; run
`benchmarks/plan68_loro_production.py` to enforce its deliberately generous p50/p95/p99 time and update-size
ceilings. Transport, sender authentication, authorization, durable storage, and task lifetime remain host
responsibilities rather than hidden adapter services.

## What was executed

The research extended the original two local-insert examples with focused integration scenarios. It did
not run the repository's full test suite.

Both pinned adapters passed:

- non-latest local text insertion inversion;
- causal and concurrent remote insertion preservation;
- concurrent remote insertion during a local deletion, followed by restoration of the deleted text;
- convergence across three replicas when three original updates and the inverse update were delivered in
  all 24 possible orders;
- deletion-token encode, process-style document reload, decode, and inverse;
- duplicate update import;
- isolated staging and stale-branch rejection;
- one action token spanning several containers: text/map/counter for Loro and text/map for pycrdt;
- immutable public text snapshots.

The focused tests also reproduced the limiting behavior rather than inferring it from documentation:

- Loro frontier reversal changed `{key: "B"}` to `{key: "base"}` when undoing the earlier
  `{key: "base"} -> {key: "A"}` action. The later remote register write was clobbered.
- A Loro shallow snapshot rooted at an action's `after` frontier made that action's token unusable. Rooting
  the shallow snapshot at the oldest retained token's `before` frontier preserved that token.
- The Loro Python binding raised the built-in `BaseException` class, not an `Exception` subclass, for a
  missing pre-shallow frontier and for corrupt frontier/update bytes.
- A pycrdt/Yrs document reloaded with GC enabled returned `True` from a reconstructed deletion undo while
  leaving the text empty. Reloading with `skip_gc=True` restored the character.
- A later pycrdt map overwrite caused the targeted earlier map undo to return `False` and preserve the later
  value. That is safe state behavior, but the boolean alone cannot distinguish an already-superseded no-op
  from unavailable retained authority.

The executable evidence is in
[`test_real_backends.py`](../../packages/squid-replication/tests/test_real_backends.py). The focused command is:

```console
uv run --locked --package squid-replication --extra loro --extra pycrdt \
  pytest packages/squid-replication/tests/test_real_backends.py --no-cov -q
```

Result at the report commit: 19 passed.

## Current release check

Loro Python 1.13.2 was the current PyPI Python binding on the audit date. pycrdt 0.14.4 was released on
2026-08-23, inside this repository's seven-day dependency soak window, so the lock remains on 0.14.2.
Version 0.14.4 only adds a Yrs 0.27.4 bump over the intervening GUID change. It was installed in an isolated
CPython 3.14.6 environment and repeated the remote selective inverse, multi-container stack-item, and
GC-loss scenarios with the same results. This report does not bypass the repository's soak policy merely to
change the experimental pin.

Primary release and API references:

- [Loro Python repository](https://github.com/loro-dev/loro-py)
- [Loro undo design](https://www.loro.dev/docs/advanced/undo)
- [Loro shallow snapshots](https://www.loro.dev/docs/concepts/shallow_snapshots)
- [Loro document types](https://www.loro.dev/docs/api/js)
- [pycrdt releases](https://github.com/y-crdt/pycrdt/releases)
- [pycrdt usage and UndoManager](https://y-crdt.github.io/pycrdt/usage/)
- [pycrdt API reference](https://y-crdt.github.io/pycrdt/api_reference/)
- [Yrs document GC option](https://docs.rs/yrs/latest/yrs/doc/struct.Options.html)
- [Yrs UndoManager semantics](https://docs.rs/yrs/latest/yrs/undo/struct.UndoManager.html)

## Correctness findings

### Action-addressable text undo passes for both

The Loro adapter retains `before` and `after` frontiers, computes the reverse diff on a current branch, and
exports the inverse as a new update. The pycrdt adapter retains the action's insertion/deletion `IdSet`s,
reconstructs a public `StackItem` against a current branch, and executes a one-item UndoManager.

Both techniques targeted the first action after later local and remote edits. Both preserved unrelated text
and converged under reordered delivery. Neither result depends on the libraries' ordinary "undo latest"
stack API.

This closes the original report's largest uncertainty for text: arbitrary retained action targeting is
possible with stable, portable backend tokens.

### Multi-container grouping is mechanically possible

Loro's frontier pair is document-wide. One commit changing text, map, and counter produced one token, and
the inverse changed all three. pycrdt's UndoManager can scope one transaction across text and map, producing
one `StackItem`.

This is not yet a pass for every public data type. Loro still needs per-type inverse policy, and pycrdt does
not supply all of the target types. It does prove that Squid does not need one history token per container.

### CRDT merge does not make registers selectively undoable

Loro maps are last-writer-wins registers. Applying the document diff from action-after to action-before at
the current frontier is a new write of the prior value; it can win over a later remote register write. This
is valid CRDT convergence and invalid Squid undo semantics.

The required Loro token must therefore record affected register paths and their action-after winning
operation identity. At inverse planning:

1. if the same action still wins the path, stage the prior value;
2. if a later operation wins, return `UndoConflict` or a type-defined safe no-op;
3. never apply the raw map portion of a document diff without that check.

Text/list insertion identities and counter increments remain semantic changes; register replacements remain
conditional changes. The adapter, not Squid core, owns this distinction.

### Retained history and compaction favor Loro

Both libraries retain deleted history when selective undo remains possible, so neither makes history free.
The important difference is authority granularity:

- Loro exposes shallow-history frontiers. A shallow snapshot rooted at the oldest retained token's `before`
  frontier preserved that and newer tokens in the executed spike. Moving the root beyond a token caused an
  explicit missing-history failure.
- Yrs GC is a document option. The pycrdt adapter must load with `skip_gc=True` before reconstructing a
  `StackItem`; otherwise deleted content may already be irrecoverable. The public Python surface does not
  provide a durable per-token compaction boundary equivalent to Loro's shallow root.

A production Loro adapter should persist the current shallow root in its token codec and classify a token
before that root as `ExpiredUndo` without calling into the failing diff path. A pycrdt adapter would need a
coarser backend epoch: expire every retained token before reloading/compacting with GC.

### Python error behavior favors pycrdt

pycrdt maps corrupt `IdSet` and update bytes to ordinary `ValueError`. Loro Python 1.13.2 raises the built-in
`BaseException` class for equivalent decode/import failures and for a diff before the shallow root. Such an
error bypasses normal `except Exception` adapter boundaries.

This is not acceptable at Squid's participant boundary. The Loro adapter must isolate calls and translate
only `type(error) is BaseException` from the binding, while re-raising cancellation and process-control
exceptions. The workaround should be removed once the Python binding exposes conventional exception types.

### Invalidation and transport do not decide the backend

Both bindings expose document/container observation surfaces, but path-granular reactive invalidation would
couple Squid dependencies to backend event-path semantics before the immutable snapshot API is stable. The
production hardening target therefore starts with one reactive version per replicated document. Container or
path granularity is a later measured optimization and is not a reason to leak either backend's mutable values.

Neither engine supplies Squid's transport, authentication, ownership, or durable outbox. The existing
`ReplicatedUpdate` envelope remains the boundary: decode and route outside the gate, apply inside the gate,
and publish outbound bytes after commit through an application-owned transport/outbox. No first application
requirement discovered in this research justifies selecting a library-specific network provider.

## Staging identity and performance

The original adapters used `fork()` or a new `Doc` with a fresh peer/client identity for every Squid action.
That is incorrect for a long-lived runtime: causal metadata grew with action count, and staging became
quadratic across a sequence of actions.

The spike now reuses one runtime replica identity in isolated branches and validates the exact base before
preparation. This keeps Loro at one peer and keeps both state vectors bounded. It also creates a sharp safety
condition: two branches from the same base generate colliding operation IDs, so stale-base rejection must
remain inside the no-await commit gate. A focused test proves the second branch is rejected before either
backend can silently accept only one of two colliding payloads.

[`plan68_backend_actions.py`](../../benchmarks/plan68_backend_actions.py) measured sequential one-character
actions on CPython 3.14.6/Linux x86-64 after the identity correction:

| Backend | Actions | Time/action | State vector/frontier | Full update | Peer count |
| --- | ---: | ---: | ---: | ---: | ---: |
| Loro 1.13.2 | 100 | 271 µs | 12 B | 299 B | 1 |
| Loro 1.13.2 | 1,000 | 597 µs | 13 B | 318 B | 1 |
| Loro 1.13.2 | 3,000 | 1.15 ms | 13 B | 334 B | 1 |
| pycrdt 0.14.2 | 100 | 116 µs | 10 B | 120 B | n/a |
| pycrdt 0.14.2 | 1,000 | 117 µs | 11 B | 1,021 B | n/a |
| pycrdt 0.14.2 | 3,000 | 114 µs | 11 B | 3,021 B | n/a |

The repeated-character workload compresses unusually well, especially in Loro, so update byte size is not a
cross-engine storage comparison. The timing trend is still meaningful: Loro documents `fork()` as O(n), and
its per-action staging cost rose with retained document/history size; pycrdt was effectively flat over this
small range.

The existing 50 KiB synthetic text benchmark after the same identity correction measured:

| Backend | Stage + prepare | Apply | Snapshot | Fresh import | Plan inverse | Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Loro 1.13.2 | 1.30 ms | 60 µs | 9 µs | 984 µs | 754 µs | 32 B |
| pycrdt 0.14.2 | 444 µs | 79 µs | 9 µs | 108 µs | 335 µs | 19 B |

These are spike medians, not service-level objectives. Before production, Loro needs a representative real
document benchmark and either an accepted size bound or a cheaper staging strategy. Fork-per-action is safe
after the identity correction but is not automatically cheap enough.

## Updated production gate

| Plan 68 criterion | Loro | pycrdt | Conclusion |
| --- | --- | --- | --- |
| Python 3.14 wheels | pass | pass | Current releases installed on CPython 3.14. |
| Staging leaves canonical state untouched | pass | pass | Same-replica branches remain isolated; stale branches are rejected. |
| One token groups several containers | pass | partial | Loro text/map/counter and pycrdt text/map executed; pycrdt lacks target rich types. |
| Target a retained non-latest action | pass for text | pass for text | Local and remote-later scenarios executed. |
| Preserve unrelated remote edits | partial | partial | Text insert/delete passes; Loro LWW-map reversal fails without a guard. |
| Unsupported inverse is a typed conflict | fail | fail | Loro can clobber a register; pycrdt GC/no-op booleans lack sufficient classification. |
| Token encode/reload | pass | pass | Deletion tokens survived a fresh document import with retained history. |
| Duplicate and reordered import | pass for text | pass for text | All 24 four-update delivery orders converged. |
| Three-replica convergence model | partial | partial | Deterministic scenario passes; backend-wide property/model suite is still required. |
| Representative performance and compaction | partial | partial | Focused costs and failure boundaries known; real workload and retention policy remain. |
| Cancellation/disposal ownership | fail | fail | Experimental engines are not yet wired through `ReplicatedScope`. |
| No backend types in reactive values | pass | pass | Public snapshots are immutable Python strings in the spike. |

Neither current adapter passes the full production gate. The table now distinguishes backend feasibility from
adapter work: Loro is the selected direction, while promotion awaits the remaining red rows.

## Required Loro hardening before production

1. Replace the text-only operation/token shape with a document token that records per-change inverse policy.
2. Add exact winning-operation preconditions for every LWW map value and list/movable-list replacement.
3. Translate Loro binding `BaseException` failures into typed corrupt-update, expired-token, and backend
   integrity errors without swallowing cancellation.
4. Persist a token schema containing backend version, action frontier pair, shallow-history root, affected
   register authorities, and codec version.
5. Coordinate shallow compaction with the oldest retained history token and prove retained/expired behavior
   across restart.
6. Run the same arbitrary-action, remote-edit, and delivery-order suite over text, list, movable list, map,
   tree, and counter. Register conflicts must change nothing.
7. Integrate prepare/apply/import with `ReplicatedScope`, update size limits, deduplication, disposal, and the
   Squid commit gate.
8. Benchmark staging and immutable snapshot conversion on real 50th/95th/99th percentile documents. Adopt a
   documented document-size bound if O(n) fork cost cannot be removed.

Until those items pass, the supported production behavior remains the deterministic counter/tagged-set
reference engine. There is still no unsafe fallback from an expired or non-invertible backend token.

## pycrdt disposition

Do not delete the pycrdt spike. It is valuable in three roles:

- a second-engine conformance oracle that prevents the SPI from accidentally becoming Loro-shaped;
- a benchmark baseline for Python transaction, update, and error behavior;
- a possible explicitly limited text/array/map adapter where the application accepts `skip_gc=True`, bounded
  documents, all-token expiry on compaction, and no native counter/tree promise.

It is not the selected generalized backend because satisfying Plan 68's counter/set/tree surface would require
Squid to supply additional CRDT algebra or expose different capabilities per backend. That defeats the reason
for selecting a proven backend to own merge mathematics.
