# ADR: Plan 68 action ledger and replicated backend gate

Status: accepted; Loro production adapter promoted 2026-08-27
Audit base: `13ce58a3755d3629e916c40cbe1d87200f5d8a31`  
Audit date: 2026-08-24

> **Backend research superseded:** the runtime decision in this ADR remains authoritative, but the original
> two-backend gate below was an interim spike. The expanded remote, restart, compaction, multi-container, and
> performance evidence plus the conclusive Loro hardening recommendation are recorded in
> [the final backend report](68-replicated-backend-report.md).

## Rebase audit

Implementation began six commits after the reviewed base. Those commits changed reactive task
confinement plus DevTools/docs/public testing exports. They did not change `StateDelta`, value-based
shared conflicts, blind history restore, participant preparation order, or bound one-shot operations.
Plan 68 therefore builds on task confinement and the newer DevTools seams while replacing the audited
transaction/history surfaces directly.

## Runtime decision

One admitted transaction owns an `ActionContext` and emits exactly one `ActionCommit` or
`ActionRollback`. Publishing transactions validate every strong addressed read by version. The
synchronous commit gate performs validation, freezes a `TransactionView`, prepares every participant,
installs cell patches, applies prepared participants, and then crosses the commit point. Notifications,
finalization, ledger sinks, and aftermath hooks occur after publication and cannot veto it.

`untracked()` controls dependency capture. `relaxed_read()` independently opts a shared read out of
commit validation. **Amended 2026-08-24**: "every strong addressed read" above shipped meaning every
addressed read, and was narrowed the same day to a cell the action also writes or one read inside
`strong_read()`. Read-only validation is opt-in, not the default; nothing else in this ADR changes,
because replicated conflict detection reads the same precondition set either way. Ordinary history retains committed conditional patch plans; undo and redo are new
actions, and redo is derived from the actual undo commit. External effects use idempotent compensation
executions and may end in `NEEDS_RECONCILIATION`.

Portable action outcomes use JSON schema version 1. They contain identifiers, timestamps, causal links,
terminal classification, and change counts. They do not contain owners, values, closures, tracebacks,
mutable backend objects, or arbitrary `repr()` output. Unknown schemas are rejected.

## Initial backend spike evidence (superseded)

The first spike pinned [Loro Python 1.13.2](https://pypi.org/project/loro/) and
[pycrdt 0.14.2](https://pypi.org/project/pycrdt/). Both publish Python 3.14 wheels for the primary
platforms audited. The shared focused test proves that a non-latest text insertion can be targeted and
inverted after a later insertion while preserving that later text, that its token can be encoded and
reloaded, and that exported updates import idempotently.

This section preserves the evidence available at the initial Plan 68 cutover. Its failing rows and
measurements were replaced by the broader executable research in
[the final backend report](68-replicated-backend-report.md); they are not the current gate result.

- Loro records the before/after `Frontiers`. Planning computes the reverse diff on a current fork and
  exports the new update. This preserved later text in the spike. Fork cost and all retained-frontier
  compaction interactions remain unmeasured.
- pycrdt records the action `StackItem` insertion/deletion `IdSet`s. Those sets have explicit binary
  codecs; rebuilding the `StackItem` against a current branch allowed a non-top selective undo. The
  binding's ordinary undo manager remains a stack API, so the adapter—not a call to “undo latest”—does
  the action targeting.

The current Loro API also has a stack-oriented `UndoManager`, and current pycrdt exposes transactions,
origins, stack items, and binary `IdSet` codecs. These observations come from executed integration
spikes, not feature-list inference.

### Initial measured spike cost

`benchmarks/plan68_backends.py` is a focused, non-pytest harness over synthetic text documents on
CPython 3.14.6/Linux x86-64. Values below are medians in microseconds; they are evidence about the
adapter shapes, not a production service-level objective. The repeated-character seed compresses
well in Loro, so exported byte size is deliberately not compared between engines.

| Backend | Text bytes | Stage + prepare | Apply | Snapshot | Import into fresh engine | Plan inverse | Token bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Loro 1.13.2 | 1,000 | 247 | 49 | 1 | 985 | 612 | 27 |
| Loro 1.13.2 | 10,000 | 395 | 65 | 2 | 1,036 | 1,216 | 27 |
| Loro 1.13.2 | 50,000 | 737 | 70 | 14 | 1,276 | 987 | 27 |
| pycrdt 0.14.2 | 1,000 | 212 | 14 | 5 | 86 | 212 | 17 |
| pycrdt 0.14.2 | 10,000 | 200 | 11 | 9 | 110 | 246 | 17 |
| pycrdt 0.14.2 | 50,000 | 514 | 13 | 14 | 156 | 395 | 17 |

The fake shipped adapter has separate Hypothesis models for three-replica delivery permutations and
semantic inverse preservation. Those models do not upgrade either text spike into a production adapter.

### Initial twelve-criterion gate record

| Criterion | Loro | pycrdt | Evidence or missing proof |
| --- | --- | --- | --- |
| Python 3.14 wheels | pass | pass | Locked extras install and the focused spike runs on CPython 3.14. |
| Staging leaves canonical state untouched | pass | pass | Shared engine-level test asserts the empty canonical snapshot before apply. |
| One token groups several containers | fail | fail | Text-only adapters expose one container. |
| Target a retained non-latest action | pass | pass | The first insertion is inverted after a later insertion. |
| Preserve unrelated remote edits | fail | fail | Later local text is covered; a two-replica remote edit is not. |
| Unsupported inverse returns typed conflict | fail | fail | Experimental adapters can still raise backend exceptions. |
| Token encode/reload | pass | pass | Both token codecs are exercised in the inverse test. |
| Duplicate and reordered import | partial | partial | Duplicate import is covered; reordered concurrent import is not. |
| Three-replica convergence model | fail | fail | Only the fake adapter runs this model. |
| Representative performance and compaction | partial | partial | Synthetic timing exists above; real workload and compaction retention do not. |
| Cancellation/disposal ownership | fail | fail | Engines are not integrated into `ReplicatedScope`. |
| No backend types in reactive values | pass | pass | Both snapshot APIs return `str`. |

The failing rows are a deliberate gate result, not deferred test cleanup. Generalized collaborative text
remains experimental and unsupported until a later ADR can change every required row to pass or explicitly
narrow the supported data types further.

### Narrowed fake-adapter scale boundary

The shipped fake engine is a deterministic reference adapter for counters and tagged sets, not a production
network/storage engine. `benchmarks/plan68_fake_replication.py` measures its deliberate operation-log costs.
Each seed item contributes one counter and one set operation.

| Seed items | Stage + prepare | Apply | Immutable snapshot | Import into fresh engine | Export bytes | Token bytes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 8 µs | 2 µs | 64 µs | 538 µs | 5,940 | 224 |
| 100 | 10 µs | 1 µs | 291 µs | 1.49 ms | 19,241 | 226 |
| 1,000 | 89 µs | 3 µs | 3.24 ms | 23.85 ms | 154,402 | 228 |
| 4,000 | 440 µs | 4 µs | 19.26 ms | 100.47 ms | 606,909 | 227 |

Snapshots and fresh import are intentionally linear in retained operations. Envelopes reject more than 1.5 MiB,
backend updates reject more than 1 MiB or 10,000 operations, pending exports retain 1,000 envelopes, and remote
deduplication retains 10,000 identities. These bounds make the adapter suitable for deterministic conformance,
examples, and small semantic documents; they are also why it is not presented as an offline database.

Within that narrowed promise the adapter passes isolated staging, multi-container action grouping, arbitrary
retained action tokens, remote-edit-preserving counter/set inverses, typed conflicts on expired authority,
schema-one token reload, duplicate/reordered delivery, a Hypothesis three-replica convergence model, immutable
public snapshots, and scope disposal. It owns no transport task; the host owns any AnyIO task and receives only
post-commit `ReplicatedUpdate` envelopes.

## Current production gate result

The explicitly injected `LoroBackend` is now the production generalized adapter. It implements named text,
list, movable-list, map, tree, exact-counter, and tagged-set handles without exposing backend containers.
Type-aware tokens limit raw frontier reversal to filtered text roots. Register replacements and moves record
an action authority and conflict the complete inverse if a later operation wins. History-held leases define
the shallow-compaction floor, including checkpoint reload; released pre-floor tokens are classified as
expired before the binding's failing diff path is called.

The adapter translates Loro's bare `BaseException` decode/import failures, bounds updates, tokens, roots,
operations, paths, and container cardinality, and rejects stale staging before canonical apply. Versioned
p50/p95/p99 build-review fixtures provide generous hard performance and encoded-size ceilings. The host still
owns transport tasks, sender authentication, authorization, and durable storage. The legacy text-only Loro
and pycrdt engines remain experimental conformance oracles.

## Atomicity limits

The commit sequence and commit gate are local to one runtime. Remote decode, authentication, storage,
and network I/O remain outside the gate. The package promises no cross-process or multi-document
visibility transaction; transports and durable outboxes remain application infrastructure.
