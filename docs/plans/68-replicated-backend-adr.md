# ADR: Plan 68 action ledger and replicated backend gate

Status: accepted, with production CRDT selection deferred  
Audit base: `13ce58a3755d3629e916c40cbe1d87200f5d8a31`  
Audit date: 2026-08-24

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
commit validation. Ordinary history retains committed conditional patch plans; undo and redo are new
actions, and redo is derived from the actual undo commit. External effects use idempotent compensation
executions and may end in `NEEDS_RECONCILIATION`.

Portable action outcomes use JSON schema version 1. They contain identifiers, timestamps, causal links,
terminal classification, and change counts. They do not contain owners, values, closures, tracebacks,
mutable backend objects, or arbitrary `repr()` output. Unknown schemas are rejected.

## Backend spike evidence

The spike pins [Loro Python 1.13.2](https://pypi.org/project/loro/) and
[pycrdt 0.14.2](https://pypi.org/project/pycrdt/). Both publish Python 3.14 wheels for the primary
platforms audited. The shared focused test proves that a non-latest text insertion can be targeted and
inverted after a later insertion while preserving that later text, that its token can be encoded and
reloaded, and that exported updates import idempotently.

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

## Production gate result

No general CRDT backend is selected yet. Both adapters remain experimental extras because only text has
passed. Rich map/list/tree/counter grouping, three-replica model tests, token survival across backend
compaction, representative document benchmarks, cancellation/ownership under transport load, corrupted
and oversized input policy, and durable restart/GC coordination are still unproven. This explicitly
narrows the initial shipped replicated behavior to the deterministic counter/tagged-set reference
engine and defers generalized collaborative text history.

There is no unsafe fallback. Applications needing a real backend may run the experimental SPI tests,
but the public package does not silently promise production support or leak a backend container. A
later ADR may select one adapter only after all twelve Plan 68 production criteria pass.

## Atomicity limits

The commit sequence and commit gate are local to one runtime. Remote decode, authentication, storage,
and network I/O remain outside the gate. The package promises no cross-process or multi-document
visibility transaction; transports and durable outboxes remain application infrastructure.
