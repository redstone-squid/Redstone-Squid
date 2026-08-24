# Plan 68 completion audit

Status: complete, with generalized production CRDT support explicitly deferred by ADR
Audit base: `13ce58a3755d3629e916c40cbe1d87200f5d8a31`  
Implementation branch: `local-development`

This matrix is the merge gate for Plan 68. A phase is complete only when its named evidence exists
and the focused checks pass. “Implemented” means the public/runtime behavior is present; “evidenced”
means the adversarial, restart, retention, or performance claim has a direct test or recorded result.

| Phase | Implemented | Evidenced | Remaining work |
|---|---:|---:|---|
| 0 — baseline and scheduler | yes | yes | Named checkpoints reproduce every audited race; standalone benchmark/ADR records 0/1/10/100-cell latency and retention. |
| 1 — identity and outcomes | yes | yes | Safe actor/relation/conflict projections and action/operation/resource/aftermath nodes have focused tests. |
| 2 — OCC and commit point | yes | yes | Full strong-read OCC, tombstone lineage, staged view, prepare cleanup, cancellation, and integrity paths are scheduler-backed. |
| 3 — conditional history | yes | yes | Weak targets, absent/recreated lineage, local-only overwrite, participant redo, and typed failures are covered. |
| 4 — causal DevTools | yes | yes | Bounded mixed-node timeline works with profiling off; retained spans link to stable action IDs when present. |
| 5 — compensation | yes | yes | Causal executions, transactional outbox SPI, schema-one restart, deduplication, cancellation, retry, and reconciliation are covered. |
| 6 — fake replicated SPI | yes | yes | Routed/hashed envelopes, bounded scope ownership, token expiry, property models, and remote commit-gate races pass. Transport tasks remain host-owned. |
| 7 — two backend spikes | yes | yes | Both locked extras pass the same text tests; timing and all twelve gate rows are recorded. |
| 8 — production adapter | deferred | yes | The ADR rejects both real adapters for production and narrows shipped behavior to the bounded deterministic counter/tagged-set reference adapter. No unsafe text API ships. |
| 9 — durability and cutover | yes | yes | Durable policies/codecs, corruption/expiry/restart/retention evidence, migration guide, examples, and breaking removal are complete. |

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
- [x] Every admitted path, including read-only and framework-integrity paths, has scheduler-backed exactly-once evidence.
- [x] Hook/sink/finalizer failures are visible as bounded causal diagnostic nodes without changing the immutable outcome.
- [x] History and diagnostic retention cannot pin unrelated component/shared owner graphs.
- [x] Compensation intent, retry, external success, local conflict, outbox failure, and restart are durable and truthfully inspectable.
- [x] Operation executions, resource generations, remote imports, undo/redo, and compensation form a reconstructable graph with profiling off.
- [x] Scope disposal leaves zero owned replicated tasks, subscriptions, documents, pending exports, and late callbacks. The reference adapter creates no task itself.
- [x] Default ledgers, histories, tokens, deduplication caches, pending exports, and compensation records have measured or directly asserted bounds.

## Final focused evidence

The development box deliberately did not run repository-wide pytest discovery.

- 41 focused `squid-reactive` action/interleaving/operation/resource tests passed.
- 106 focused `squid-layouts` transaction/history/DevTools/operation tests passed.
- 32 fake/property/real-backend `squid-replicated` tests passed.
- 33 focused form-submit, profiler-link, and portable responder tests passed.
- Both Loro and pycrdt extras passed their four engine-level spike tests within the replicated slice.
- Pyrefly ran once; filtering its known nonzero repository result to Plan 68 package and benchmark paths produced no findings.
- Ruff check/format covered all 40 Python files changed since this completion audit opened.
- `alembic heads` reported the single expected head `f8a4c7d2b5e9`; `git diff --check` passed.

The accepted performance and backend decisions are
[ADR 0068](../decisions/0068-action-ledger-performance.md) and
[the replicated backend ADR](68-replicated-backend-adr.md). The latter is the required explicit
deferral: neither experimental text adapter met the production gate, so Squid does not claim it did.

## Validation policy

Do not run the repository-wide pytest suite on the development box. Run focused files with
`--no-cov`, both real-backend spike tests with package extras, `just typecheck` with touched-file
triage, changed-file Ruff, `alembic heads`, and `git diff --check`. Record the deliberately small
benchmark separately from correctness tests.
