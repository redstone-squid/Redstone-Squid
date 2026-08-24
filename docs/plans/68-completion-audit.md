# Plan 68 completion audit

Status: active implementation audit  
Audit base: `13ce58a3755d3629e916c40cbe1d87200f5d8a31`  
Implementation branch: `local-development`

This matrix is the merge gate for Plan 68. A phase is complete only when its named evidence exists
and the focused checks pass. “Implemented” means the public/runtime behavior is present; “evidenced”
means the adversarial, restart, retention, or performance claim has a direct test or recorded result.

| Phase | Implemented | Evidenced | Remaining work |
|---|---:|---:|---|
| 0 — baseline and scheduler | partial | no | Add deterministic commit checkpoints and a reproducible 0/1/10/100-cell latency/allocation baseline. |
| 1 — identity and outcomes | partial | partial | Retain safe actor/relation/conflict data, emit post-hook failures, and represent operation/resource causal descendants. |
| 2 — OCC and commit point | yes | partial | Exercise all named interleavings through the deterministic scheduler and retain prepare-abort cleanup failures. |
| 3 — conditional history | partial | partial | Use weak cell targets, cover absent/recreated slots, and add the named local-only overwrite policy. |
| 4 — causal DevTools | partial | partial | Display operation/resource and aftermath-failure nodes independently of profiler retention. |
| 5 — compensation | partial | partial | Add causal execution contexts, committed intent transitions, an application-owned outbox SPI, restart recovery, and duplicate-dispatch tests. |
| 6 — fake replicated SPI | partial | partial | Add document/action/origin update envelopes, token expiry, owned transport tasks/subscriptions, and broader model tests. |
| 7 — two backend spikes | yes | partial | Record representative stage/import/snapshot/token benchmarks and all production-gate results, not only text semantics. |
| 8 — production adapter | narrowed | partial | The accepted backend ADR explicitly narrows shipping to the deterministic counter/tagged-set backend and defers generalized collaborative text. Prove every promise of that narrowed scope. |
| 9 — durability and cutover | partial | partial | Add durable sink/outbox examples, corruption/expiry/restart/retention tests, direct before/after migration examples, and final leak/performance evidence. |

## Invariant evidence checklist

- [x] Failed handlers and participant preparation publish no staged reactive or fake-replicated state.
- [x] Normal success, handler exception, cancellation, OCC conflict, and prepare failure emit one terminal action outcome.
- [x] Publishing actions validate every strong addressed read by version; A→B→A conflicts.
- [x] Undo and redo are new actions and redo is based on the actual committed undo.
- [x] Mixed state-only inverses prepare in one runtime transaction and conflict without partial publication.
- [x] Local and fake-remote imports pass through the same runtime commit gate.
- [x] Participant preparation receives a frozen staged view before canonical publication.
- [x] Participant apply failure is classified as framework-integrity damage rather than a safe rollback.
- [x] Direct aftermath mutation is rejected; recovery starts a new causal transaction.
- [x] Mutable backend containers do not escape the fake or text-spike APIs.
- [ ] Every admitted path, including read-only and framework-integrity paths, has scheduler-backed exactly-once evidence.
- [ ] Hook/sink/finalizer failures are visible as bounded causal diagnostic nodes without changing the immutable outcome.
- [ ] History and diagnostic retention cannot pin unrelated component/shared owner graphs.
- [ ] Compensation intent, retry, external success, local conflict, and restart are durable and truthfully inspectable.
- [ ] Operation executions, resource generations, remote imports, undo/redo, and compensation form a reconstructable graph with profiling off.
- [ ] Scope disposal leaves zero owned replicated tasks, subscriptions, documents, pending exports, and late callbacks.
- [ ] Default ledgers, histories, tokens, deduplication caches, and compensation records have measured bounded retention.

## Validation policy

Do not run the repository-wide pytest suite on the development box. Run focused files with
`--no-cov`, both real-backend spike tests with package extras, `just typecheck` with touched-file
triage, changed-file Ruff, `alembic heads`, and `git diff --check`. Record the deliberately small
benchmark separately from correctness tests.
