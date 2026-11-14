# Reuse, diagnostics, and performance

Status: implementation in progress.

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
selection cost depends on region count, not the number of certified combinations.
Hints must stay within the requested work limit and preserve legality and complete
search results. Timing measurements are evidence, not machine-dependent CI gates.
