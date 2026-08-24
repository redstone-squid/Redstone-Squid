# ADR 0068: Accept the measured action-ledger base-path cost

Status: accepted (2026-08-24)

## Context

Plan 68 requires every admitted transaction, including a no-op, to allocate a globally useful action identity and an
immutable terminal outcome. Publishing transactions also freeze their complete read set and stable, weakly held slot
lineage. Those guarantees add work that the reviewed commit did not perform.

The reproducible microbenchmark is `benchmarks/plan68.py`. It uses neither pytest nor coverage and measures the median
of seven batches with garbage collection disabled during timing. The reviewed baseline was run from commit
`13ce58a3755d3629e916c40cbe1d87200f5d8a31`; the new results were run from this branch on CPython 3.14.6, Linux x86-64.

| Strongly read and written cells | Reviewed median | Plan 68 median | Ratio |
| ---: | ---: | ---: | ---: |
| 0 | 10.1 µs | 52.3 µs | 5.2x |
| 1 | 22.2 µs | 87.3 µs | 3.9x |
| 10 | 102.4 µs | 254.9 µs | 2.5x |
| 100 | 908.2 µs | 1,845.9 µs | 2.0x |

The bounded retention measurement allocated approximately 62.8 KiB for 100 safe ledger snapshots and 144.7 KiB for
100 conditional local history entries. These numbers are process-allocation deltas, not serialized sizes.

## Decision

Accept this cost for the coordinated breaking release. The absolute hot-path cost is under 0.1 ms for the common
zero/one-cell cases and under 2 ms for the deliberately broad 100-cell case. Discord network latency and rendering
dominate those values, while removing action identity, outcome construction, version reads, or weak target lineage
would violate correctness invariants.

Keep three optimizations that preserve semantics:

- do not construct a portable snapshot when no outcome sink is registered;
- bypass deterministic checkpoint context lookups when no harness is installed;
- skip empty notification/finalization work and avoid publishing an epoch for a no-op transaction.

The benchmark is a regression sentinel, not a stable public performance promise. A future change that increases a
scenario by more than 20% on the same runner requires investigation and either an optimization or a superseding ADR.
Absolute comparisons between different hosts are not meaningful.

## Consequences

Action-heavy applications pay a fixed identity/outcome cost even when an action writes nothing. Applications may
filter no-op snapshots from retention, but the transaction still has a terminal outcome. The default ledger, history,
and compensation outbox remain bounded so the release does not exchange latency correctness for unbounded memory.
