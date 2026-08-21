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

Agreed 2026-08-21 (external audit, verified in-repo); not started.
