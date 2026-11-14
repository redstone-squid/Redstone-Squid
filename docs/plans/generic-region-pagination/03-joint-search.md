# Shared allocation search

Status: planning. The product decisions below are agreed; implementation has not started.
This phase replaces explicit selection with joint representation and page allocation search
through the [phase 2 composition boundary](02-composition.md#phase-3-handoff).

## Scope

Search over authored alternatives, target representations, and legal page partitions together.
Account for persistent content, repeated containers and navigation, nested regions, structural
budgets, shared assets, and competing target limits. Exercise V2 and Slack messages directly,
with HTML establishing behavior without a global message budget.

Return an immutable allocation and page structure that phase 4 can use for cursor reconciliation
and delivery. Selecting a different page must not require choosing a different allocation.
The solver consumes semantic and composition contracts; it does not inspect target scene bodies.

Mounted runtime integration, ownership migration, and publishing presentation state remain phase 4.
Cross-request caching and performance tuning remain phase 5. Completing the target and widget
matrix and removing unmigrated production paths remain phase 6. Phase 3 must still expose usable
search through the opt-in composition entry point and establish bounded work and diagnostics.

## Agreed behavior

### Preference and fidelity

Hard requirements and loss permissions determine which choices are admissible. Strict mode rejects
actual loss even when the author prefers it. In non-strict mode, an explicit preference may favor
an alternative with authorized loss over a lossless alternative. A lower preference number wins;
the numbers express ranks within an alternatives group, not additive penalties. Changing
`0, 1, 2` to `0, 10, 20` must not change the result.

Among equally preferred choices, favor preserving detail. Favor fewer page turns when fidelity
is comparable. Do not assign an invented importance score to omitted fields, shortened prose,
or changed interactions. Permission to apply an effect is not a claim that it is unimportant.

Equally important sibling regions should share the compromise: both receiving their second choice
is preferred to one receiving its first and the other its third. First/third remains valid when
second/second cannot fit. Explicit region priority can favor one region; richer controls for
trading preference across regions are deferred until a concrete use case needs them.

### Allocation and page consistency

Preserve the existing preference for stable allocations. Cursor-only movement must keep sibling
allocations, page boundaries, and chosen presentations unchanged. A short final page does not
donate its unused space to a sibling. Changed content or constraints can require a new allocation;
phase 4 owns reconciliation with the reader's prior position.

Use a consistent presentation for a paginated semantic occurrence across its pages by default.
This does not require unrelated widgets in one region to use the same representation. Page-specific
adaptation or space reclamation needs an explicit policy; neither is an automatic fitting fallback.
Those optional policies need not ship in this phase.

Give regions useful minimum allocations before distributing spare capacity according to author
preferences. Default minimums are soft, with explicit hard minimums available. Every nonempty
visible region must nevertheless show at least one complete legal content unit on each page.
An unbreakable group may make that unit larger than one authored child. The solver must not invent
collapsed regions or empty navigable pages to make a layout fit. An actually empty region is valid.

Preserve the meaning of existing hard constraints during migration: the current `sl.budget(min=...)`
declares a hard floor. Introducing soft defaults does not silently weaken that existing declaration.
Keep allocation targets separate from structural resource limits and presentation preference ranks.

### Search outcomes

Return the best fully validated layout found if the work allowance runs out. Attach a structured
note recording exhaustion and that optimality is unproven; log this at DEBUG, without a user-facing
warning. Ordinary bounded search can reach this outcome without a layout defect.

If no valid layout has been found, exhaustion remains a distinct failure. Only a completed search
over the supported candidate space, or a sound impossibility proof, can establish infeasibility.
Unsupported capabilities and invalid declarations retain their own outcomes. A target construction
mismatch is an invariant failure, not permission to silently repair or discard the mismatch.

Optimality and infeasibility claims apply to the declared representations and legal breaks available
to this search. They do not claim that no other imaginable rendering of the content exists.

## Current evidence and required extensions

Paths in this section are relative to `packages/squid-ui/src/squid_ui/planning/`.

- `composition.py` exposes occurrences, offers, candidate trees, contextual evaluations, and immutable
  target preparations. Reuse this boundary; target scene bodies remain opaque to search.
- `RealizationOffer` has children, effects, and preference, but no break or continuation contract.
  `CandidateComposition` requires exactly the offer's declared children. Introduce explicit fragment
  occurrences rather than paginating by silently slicing candidate children.
- `realizations.py` exposes complete tables and represents regions as ordinary flow containers.
  Preserve region structure during search and expose table row breaks with repeated column headings
  and source identity.
- `build_composition` performs preflight, evaluation, shared-policy checks, construction, and independent
  validation together. Separate admission/evaluation from construction without bypassing those checks.
- `WorkBudget` spans composition operations, while legacy `regions.py` has separate measurement and
  squared search allowances. Extend the composition allowance to enumeration and certification.
- Legacy region frontiers compare numeric envelopes, degradation, turns, and summed preference.
  Shared identities and ordinal preferences make that dominance relation unsuitable for the new solver.
- `balanced_breaks` solves an additive sequence problem with hard break boundaries and soft
  `min_fill`/`widows`. It cannot settle a joint problem with contextual costs or shared assets.
- `PlanMetrics.states_explored` documents measurement calls, but fixed composition stores total work
  there. Give the new path explicit work and search counters instead of extending that ambiguity.

Existing evidence includes `test_candidate_composition.py`, `test_composition_targets.py`, and
`test_resource_budgets.py` in `packages/squid-ui/tests/`, plus `packages/squid-ui-discord/tests/test_regions.py`.
They cover shared identities, contextual nesting, construction mismatches, sibling page combinations,
nested visibility, stable allocation, and a small additive allocation oracle. They do not establish
correctness of a generic joint solver or its new ranking policy.

## Search design

### Candidate space and stable page families

A complete candidate chooses representations and legal partitions for the whole region tree.
It includes persistent content, continuation shells, headers, footers, and navigation for every page.
An independently paged sibling can visit any of its pages. A nested region contributes visible
content and navigation only on parent pages that contain it. Certify those reachable combinations;
do not require combinations of mutually hidden content to fit together.

Begin with exact evaluation of reachable combinations, generated lazily. A candidate is usable
only after all its combinations have been certified. A conservative resource envelope may establish
that they all fit, but an oversized envelope is not proof that a real combination overflows.
Shared assets must be deduplicated within each visible structural budget, with conflicts checked
only where the uses coexist. Numeric maxima alone cannot carry this information.

Extend the typed contracts with legal fragment boundaries, source coverage, continuation requirements,
and navigation occurrences. Keep fragments of one semantic occurrence tied to its presentation
choice unless the author has explicitly allowed variation. Preserve `unbreakable`, `keep_with_next`,
structural budgets, and region boundaries through alternatives and wrappers. Atoms remain indivisible
unless a richer contract supplies their structure. Do not infer breaks from opaque target payloads.
Offer discovery must distinguish a complete set of choices from unsupported vocabulary or interrupted
enumeration; an empty tuple alone cannot explain all three outcomes.

Fragmentation must neither lose nor duplicate ordinary source content. Repeated headings and controls
need explicit continuation roles and distinct placement identities. Carry source anchors separately
from fragment IDs so phase 4 can reconcile positions without treating a new page as new content.
Navigation extent, labels, disabled states, and container placement participate in evaluation;
there is no constant navigation allowance assumed to work for every target or page count.

### Comparing candidates

Implement the agreed ordering explicitly rather than reusing `search.CostVector` or adding raw
preference values. Normalize each group's distinct declared preference values into dense ranks,
retaining ties. Normalize before candidate feasibility filtering so rejection of a preferred choice
does not disguise a compromise. Count a logical choice once, not once per page or repeated header.

The proposed fairness comparison is lexicographic minimization of worst dissatisfaction first.
Within a region, sort its active choice ranks from worst to best. Compare these vectors with missing
entries treated as zero. Among equal-priority sibling regions, sort their vectors from worst to best
and compare in that order. Higher explicit priority tiers are compared first. Apply the grouping
recursively for nested regions; do not let one region gain priority simply by having more rows.
For two regions with one choice each, ranks `(1, 1)` therefore beat `(0, 2)`. The numeric preference
gaps never enter the comparison. Step 1 must exercise conditional alternatives and unequal numbers
of choices before this proposed generalization becomes the implemented contract.

Compare soft allocation and break-policy satisfaction before page-count improvements. Represent
allocation goals in their declared units, separate from preference ranks; a resource cap is never
a soft goal. Preserve `min_fill` and `widows` as soft preferences rather than discarding them or
turning them into hard requirements. The exact ordering of soft allocation goals against presentation
preferences remains a design checkpoint in step 1: our discussion established useful minimums first,
but did not assign precedence to every combination of author hints.

For tied preferences, lossless dominates lossy. Among lossy candidates, use only established semantic
comparisons, such as an otherwise identical candidate with a strict subset of the same source-scoped
effects. Different omitted fields or unrelated interaction changes need not be comparable. Keep
those candidates separate; do not count effects as a measure of importance. Minimize total page
turns, `sum(max(0, pages - 1))` over logical regions, within comparable fidelity classes. This is a
navigation proxy, not an estimate of how often a reader visits each nested region.

Use stable occurrence paths, offer keys, and cut positions to resolve remaining equivalent choices.
For incomparable fidelity outcomes, select deterministically from the nondominated results without
claiming one preserves more meaning. An optimal result means no candidate is better under the
specified comparison, not that every valid layout has a unique quality score. Oracle tests must
check the nondominated set as well as the deterministic selected result.
Retain incomparable outcomes until dominance is settled; keeping only the current deterministic
winner could lose an outcome needed after a later candidate dominates that winner.

### Algorithm and sound pruning

Start with lazy branch-and-bound enumeration of partial assignments and legal partitions. Use a
deterministic best-first work queue to try promising assignments early and retain a validated best
result while search continues. Generate each conditional offer's children only when that branch is
visited; do not first materialize the Cartesian product of choices or page combinations.

Use ranking and fit estimates to order work. Prune only with a separately justified bound or proof:

- Reject a complete candidate on its actual violations. A partial candidate's overflow is sufficient
  only when a proven lower bound shows every completion also overflows.
- Discard a partial candidate on ranking only when its optimistic bound cannot improve the retained
  result, including the final deterministic tie policy. Incomparable fidelity is not such a proof.
- Merge identical states only when they admit the same future choices and retain the same source,
  effects, region structure, constraints, context, resource identities, and interaction ownership.
  Where equality cannot be established for an opaque extension, keep both states.

Initially prefer weak or absent bounds over speculative dominance. Equal scalar costs or a cheaper
standalone representation do not justify pruning: another region may share the expensive candidate's
asset, and container context can change feasibility. Each stronger rule needs a counterexample test
and an exhaustive comparison. Never truncate a frontier and then report established infeasibility.

This choice avoids extending the legacy additive frontier beyond its assumptions. An additive
dynamic program remains useful for proven independent sequences; a beam search would sacrifice
completeness even with ample work. Pure exhaustive enumeration supplies the test oracle, but useful
search order and sound bounds should find usable production results earlier.

Completeness is conditional on finite declared offers, finite legal fragment boundaries, complete
target enumeration, and enough work to drain the queue. Unsupported fragmentation is reported as
unsupported, not proved impossible. Exhaustion makes no completeness or optimality claim. Phase 3
must include a representative larger case to prevent an exact but unusable default search; broader
performance targets and cross-request reuse still belong to phase 5.

### Work limits and retaining a usable result

Use one allowance for semantic traversal, offer discovery, state generation, page-combination
evaluation, policy checks, preparation, building, validation, and result assembly. Charge loops and
queue admission incrementally, before doing the work; shared and target operations cannot reset
the allowance. A work unit bounds declared operations, not wall-clock time for arbitrary custom
Python. Finite offers alone do not excuse uncharged work inside a target extension.

Keep a result eligible for return only after its entire page family is certified and its requested
scene is built and independently validated. Store its body, bindings, resources, page structure,
and diagnostics together. If evaluating or finalizing a better candidate exhausts the allowance,
return the earlier scene with the updated search status. If no such result exists, report exhaustion. Do not spend
unaccounted work after exhaustion to turn an evaluated candidate into a deliverable result.

The structured outcome records completion or exhaustion, whether optimality is established, work
used and allowed, and the available candidate violations. Keep generated states, target evaluations,
and certified page combinations as separate counters. Emit the DEBUG exhaustion log once at the
public planning boundary. Building a discarded candidate must not publish bindings or mutate
presentation state, and construction mismatches must still propagate as invariant failures.

## Reviewable implementation sequence

1. **Define comparison and outcomes.** Add typed allocation policy and search outcome contracts,
   dense preference ranks, and examples for fair compromise, authorized loss, and deterministic
   ties. Resolve the soft-goal ordering and conditional-choice comparison checkpoints above using
   concrete fixtures; discuss any resulting policy change before implementation. Keep region
   priority ordinal and distinct from legacy `stretch`. Establish the independent exhaustive oracle.
2. **Expose legal fragments and navigation.** Extend semantic occurrences and target offers with
   region boundaries, break units, continuation roles, and navigation structure. Separate evaluation
   from construction while retaining all phase 2 admission, atomic, budget, and binding checks.
   Exercise fixed fragment configurations on V2, Slack messages, and HTML before adding search.
3. **Implement joint enumeration.** Search representations and partitions together; certify all
   reachable page combinations and retain stable allocation structures. Start with conservative
   pruning and cover nested visibility, multiple axes, repeated chrome, and shared resources against
   the oracle. Keep target-specific body construction outside the solver.
4. **Bound work and retain validated results.** Charge each search and target operation to the same
   allowance. Inject exhaustion during discovery, combination certification, building, and validation.
   Return a prior validated result where available; distinguish unsupported, infeasible, and exhausted
   outcomes. Add structured diagnostics and one DEBUG log at the boundary.
5. **Expose automatic planning and hand off.** Extend the opt-in composition entry point with automatic
   search and explicit page coordinates for testing. Preserve fixed selections as reproducible test
   inputs through the same evaluation path. Pass immutable allocations and anchors to the phase 4
   boundary without migrating mounted state now. Update the API sketch and migration inventory to
   implemented names, run focused conformance and regression checks, and record remaining limitations.

Each step should leave an independently valid commit. Do not route automatic search through legacy
complete planners, silently weaken authored policies, or advertise full pagination migration at step 5.

## Validation

Build the exhaustive oracle independently of production ranking and pruning helpers. On small finite
fixtures, enumerate every offer assignment, legal partition, and reachable page combination. Compare
feasible sets, nondominated outcomes, and deterministic selections with the solver under sufficient
work. With bounded work, assert result validity and accurate completion status instead of optimality.

Acceptance fixtures must cover:

- Preference gap changes and offer reordering; second/second versus first/third; explicit priorities;
  tied preferences; nested and conditional choices; repeated content counted once for ranking.
- Preferred authorized loss in non-strict mode, strict rejection, detail preservation on a preference
  tie, and incomparable losses without invented importance scores.
- Soft minimum shortfalls versus hard minimum failures, one complete unit per visible region,
  empty regions, impossible atoms, hard breaks, `min_fill`, and `widows` through wrappers.
- Stable presentations and page boundaries at every sibling coordinate, including short final pages,
  nested hidden children, repeated headings and navigation, and page-count-dependent chrome costs.
- Shared attachments that reverse standalone cost rankings, equal counts with different identities,
  conflicting co-visible uses, separate budget scopes, and multiple simultaneous resource limits.
- Exhaustion before any result and after a valid result, including during replacement construction;
  no uncharged finalization, no state publication, and fatal construction/evaluation mismatches.
- Direct V2 and Slack participation with legacy planners disabled; HTML without invented global limits;
  unsupported target features reported distinctly from exhausted search and infeasible candidates.

Run focused tests with `--no-cov` during implementation and after final relevant edits, plus changed-file
format/lint hooks, `git diff --check`, and whole-project `just typecheck`. Keep all workspace source
directories in Pyrefly's search path. The full suite remains CI's responsibility unless impact warrants
a broader local run.

Planning validation: the 68 existing tests in the candidate composition, composition targets, resource
budgets, breaking, and Discord regions files passed with `--no-cov`; whole-project Pyrefly reported
zero errors. This is baseline evidence, not validation of the proposed algorithm. Implementation
completion requires the new oracle and acceptance cases above.

## Completion criteria

- Automatic representation selection and joint fragmentation use the shared composition boundary.
- All reachable page combinations satisfy target limits, local constraints, and enclosing budgets.
- Stable allocations and presentations do not depend on the currently selected page.
- Preference ordering, fair compromise, fidelity defaults, and hard requirements have direct tests.
- Small cases agree with an independent exhaustive oracle, including non-additive resource cases.
- One work allowance covers shared and target operations, including returning a validated result.
- Exhaustion with a valid result, exhaustion without one, and established infeasibility are distinct.
- V2, Slack messages, and HTML exercise the solver without calling a legacy complete target planner.
- Phase 4 receives immutable page structures, source anchors, visible binding information, and a
  documented boundary for state integration. Planning publishes no state or handler registrations.

The phase 4 handoff must document stable allocation identity, how to select a visible page without
reallocating siblings, source anchors, nested visibility, navigation inputs, and when content or
constraints invalidate an allocation. It must also list remaining production routes and their removal
phases. No phase 3 result commits runtime state; successful delivery remains the publication boundary.
