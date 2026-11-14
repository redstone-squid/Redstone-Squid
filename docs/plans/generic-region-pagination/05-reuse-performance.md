# Reuse, diagnostics, and performance

Status: implemented and validated.

## Evidence and decisions

Phase 4 retains an owner's delivered allocation, but repeats its search counters
on page turns. PlanCache is ignored by shared planning. Certified page selection
scans every visible combination twice during presentation.

Keep committed allocations as the fastest reuse path. Add a bounded, callback-free
partition-hint store to PlanCache for cross-request reuse. Hints change search order,
never the search domain: current offers, legal cuts, resources, navigation, and
bindings must be certified again. This avoids storing live handlers or trusting a
fingerprint as proof of equivalence. Routed pages bypass hints. Unknown or changed
inputs can at worst supply an inapplicable hint, which is ignored.

Do not cache entire certified scenes across owners or constrain a new search to a
cached winner: both would require a stronger equivalence contract than current
fingerprints provide. Preserve the exhaustive search and bounded-work guarantees.

## Implementation and validation

1. Add bounded structural hints and integrate them into automatic shared planning.
   Validate hint legality before queueing it within the shared work budget.
2. Report retained allocation reuse with zero new search counters, keeping original
   certification diagnostics in the report. Index certified combinations for turns.
3. Test fresh callbacks, invalidation, stale hints, cache eviction, and cursor reuse.
   Measure many items, nested regions, competing limits, and repeated turns.
4. Run focused tests, formatting and lint hooks, whole-project type checking, and
   diff checks. Record measured results and phase 6 limitations here.

Performance expectations: cursor turns perform no search or target evaluations;
selection cost depends on the visible-region sets and their region counts, not the
number of sibling page combinations.
Hints must stay within the requested work limit and preserve legality and complete
search results. Timing measurements are evidence, not machine-dependent CI gates.

## Outcome and measurements

PlanCache now retains bounded LRU partition hints for automatic built-in composition,
including production pagination. A fingerprint includes content and authored search
options but excludes selected coordinates. A changed callback can still find a hint;
no cached callback, resource object, scene, or route encoding is replayed. Target or
navigation changes may reuse cuts as a hint, but must pass current certification.
Invalid, out-of-range, or incompatible cuts are ignored. Fixed partitions remain
mandatory. Hints add a candidate without removing any legal search branch.

Fingerprint traversal, hint validation, and construction of the page index consume
the same operation allowance as semantic traversal and certification. The allowance
counts abstract operations, not bytes or wall time. Migration, retained-input
comparison, and presentation reconciliation remain outside search accounting;
retained turns still traverse input and anchor data. Metrics describe search work,
not a claim that a turn does no CPU work.

`PlanMetrics.reuse == ALLOCATION` identifies a retained turn, including `for_page`.
Its search counters are zero. `PlanReport.search` preserves the original certificate's
completion and optimality, while region reports describe the newly selected pages.
`partition_hint` records a structural-cache lookup hit; it does not claim that a hint
was valid, won, or avoided certification. Certified output is indexed by visible-region
set and coordinates, so sibling Cartesian products no longer require a linear scan.
Nested layouts can still require checking multiple visible-region sets.

Reproduce with `uv run scripts/benchmarks/region_pagination.py`. One local run on
Python 3.14 produced these timings; turn times average 50 calls. Cold and hinted
planning include Python warmup effects, so their timings are not evidence of a cache
speedup. Work counters are the reproducible comparison.

| Workload | Cold ms | Hinted ms | Turn ms | Cold operations | Hinted operations | Completion |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 100 items, ten mandatory pages | 34.90 | 11.37 | 1.93 | 16,010 | 16,236 | Complete |
| Nested regions | 6.20 | 5.09 | 0.72 | 2,346 | 2,411 | Complete |
| Component and text limits | 625.86 | 596.16 | 0.36 | 200,000 | 200,000 | Exhausted with certificate |
| Long text | 141.85 | 136.26 | 0.17 | 199,995 | 200,000 | Exhausted with certificate |

All repeated turns perform zero search operations and target evaluations. Hints
have a small bookkeeping cost and do not promise fewer operations. The bounded
constrained workloads retain valid layouts without claiming optimality. Keep the
existing default allowances: these measurements do not justify increasing them.

## Validation and phase 6 handoff

Regression tests cover cache eviction and immutable hints, current callbacks across
owners, changed content and limits, stale cuts, fixed partitions, complete-search
winner preservation, routed factory freshness, bounded exhaustion, and accurate turn
metrics. Existing nested selection, anchor recovery, target composition, exhaustive
search, and rendering-cache tests remain regression coverage. Whole-project Pyrefly,
focused pytest with `--no-cov`, Ruff, installed commit hooks, and diff checks pass.

Phase 6 retains the capability work listed in phase 4. Custom composition rules do
not use the built-in partition cache. Cross-owner reuse deliberately recertifies all
outputs; caching complete certificates would require a stronger equivalence contract
for navigation factories, assets, and target extensions. The operation budget bounds
search rather than all frontend preprocessing or presentation work. Large constrained
searches can still exhaust their allowance with an unproven optimum, as reported.
