# 22 — Global fit-aware strategy search

## Problem

Semantic adapters choose their lossless representation before knowing the resource
pressure from the rest of the document. Verified (external audit 2026-08-21, confirmed
against the code):

- `_action_strategy` (`planning/adaptation.py`) prunes its `individual` candidate only
  against the *absolute* component cap — `individual_components >
  context.limits.total_components` — never against the document's remaining budget.
- `_table` and `_media` strategy picks consult display preference and session memory
  only; no budget at all.
- After solve, the over-budget path (`planning/planner.py:412`) has three exits and none
  re-enters strategy selection: local pagers present → `UnsolvableLayoutError`; keyless
  document or no nav → `UnsolvableLayoutError`; otherwise root pagination.

Consequence: a 35-component document plus a five-action node picks `individual`
(preferred at ≤5 actions), lands at 41 components, and fails or root-paginates — while
`grouped` (~2 components, zero pagers) fits losslessly and would *win* the existing
`CostVector` ranking on the `active_pagers` tier, had the search ever evaluated the
assignment. With a local pager present, it is a hard failure whose remedy message
lectures the author about restructuring a document that fits.

So the advertised invariant — a legal lossless representation is found when one exists —
is not yet true. The 512-state budget is spent as a counter across independent local
greedy picks, not as a search.

## Design

> Strategy selection is a global assignment: adapters nominate candidates, assignments
> are ranked by the existing lexicographic cost, and one is accepted only when the whole
> document actually solves.

1. **Adapters stop choosing; they nominate.** Each strategy-bearing node — `Actions`,
   `Table`, `Media`, `Navigation`, `Items` — contributes its candidate axis with
   per-candidate facts. `StrategyCandidate` is already this shape.
2. **Bounded best-first enumeration** over assignments in existing `CostVector` order.
   Hysteresis is preserved for free: baseline mismatch and `Flexibility` already price a
   strategy change, so re-planning under unchanged pressure keeps remembered strategies.
3. **Feasibility is the real whole-document solve** of the lowered assignment, not a
   local estimate. The first feasible assignment in cost order wins.
4. **Ladder ordering preserved**: lossless strategy search first; then author-granted
   degradation (`Variants` collapse, truncate/spill); root pagination last. This makes
   the ranking `active_pagers` already encodes actually reachable.
5. **The search budget gets its real meaning**: states are solve attempts. Exhaustion
   keeps today's local-greedy assignment and reports `planner.search_fallback`, so
   behaviour under exhaustion is exactly current behaviour, never worse.
6. **Sequencing: before or with plan 20.** Glue budgets and region pagination multiply
   cross-node interactions; building them on locally greedy selection deepens the hole.
   This plan is the concrete charter for 20 §8's global redesign pass.

## Implementation contract

The search boundary is the planner, not semantic lowering. A `StrategyAxis` records the
semantic path, adapter identity/version, flexibility, remembered baseline, preferred
strategy, and finite candidates for one node. Paths identify occurrences within one
plan; semantic keys remain the durable identity written to `PresentationSession`.

`iter_assignments` walks the Cartesian product of the axes with a heap. Each axis ranks
its candidates by `CostVector`; the heap starts at the tuple of local minima and advances
one axis at a time. Assignment costs sum the six numeric tiers and use the complete
path/strategy tuple for deterministic ties. This is best-first without materializing an
exponential product.

One attempted assignment owns one fresh `CursorCoordinator`, semantic lowering, target lowering,
validation, and exact solve. Nothing from a rejected attempt is committed or reused. The
planner keeps the first component-feasible degraded result but continues looking for an
assignment whose solve did not consume a degradation rung. Therefore the observable
ladder is:

1. cheapest lossless, whole-document-fit assignment;
2. cheapest component-fit assignment after author-granted degradation;
3. root pagination of the cheapest assignment, under the existing root-pager rules.

The attempt budget is checked before accepting the state that consumes its final slot
when more assignments remain. At that boundary the planner returns the local-minimum
assignment and adds `planner.search_fallback`; this preserves the previous result and its
failure behavior when the global guarantee could not be completed. A cache entry records
the winning path-to-strategy assignment so a hit can re-lower current callbacks without
re-running feasibility solves.

Candidate validity remains adapter-owned. An adapter may omit a mechanically illegal
shape (for example, more individual action components than the target can ever hold).
Selection/open state remains authoritative for `Items`: global strategy search must not
reopen an item after Back or ignore a controlled selection. Every other display enum is a
preference, as it was before this change, rather than a hard constraint.

## Rejected

- **Remaining-budget estimates fed into local adapters** ("context pressure"): still
  greedy in document order — an early node eats what a late node needed — and the
  estimate drifts from the solver's real accounting.
- **Solve-then-retry loops re-running individual adapters on failure**: reintroduces the
  sequential bias with extra machinery; candidates → search → solve feasibility is
  simpler and complete.

## Verification

- The audit's construction as a regression test: dense document + five-action node →
  `grouped` chosen, zero pagers, no error; the same node with room → `individual`
  (preference honoured when feasible).
- Hysteresis: re-planning with unchanged session and content keeps remembered
  strategies — no flapping.
- Exhaustion: a search budget of 1 reproduces today's local-greedy result plus the
  `planner.search_fallback` event.
- Existing adaptation and pagination suites pass unchanged.

## Status

Implemented 2026-08-21. The planner now enumerates path-keyed assignments in aggregate
`CostVector` order, gives every attempt isolated lowering/pager state, prefers an
undegraded whole-document fit, and caches the winning assignment. The audit construction
and its stronger local-pager form both select grouped actions in two attempts; a roomy
document still selects individual actions in one.
