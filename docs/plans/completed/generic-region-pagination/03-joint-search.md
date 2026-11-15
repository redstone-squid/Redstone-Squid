# Shared allocation search

Status: implemented and validated. Automatic composition is opt-in; mounted state integration remains phase 4.
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

## Implemented boundary

Use `CompositionOptions(automatic=True)` with `plan`. V2, Slack messages, and HTML use their
composition rules directly. `AutomaticComposition(rules, options)` provides the same search for
custom typed rules. `CompositionOptions()` and `FixedComposition` retain the phase 2 fixed-selection
behavior. Fixed selections and partitions can also constrain automatic search for reproducible tests.

The implementation is in `packages/squid-ui/src/squid_ui/planning/`:

| Module | Responsibility |
| --- | --- |
| `allocation_policy.py` | Dense preference ranks, recursive fairness, fidelity comparison, goals, diagnostics. |
| `composition.py` | Offer discovery, fragment and region contracts, admission, construction, independent validation. |
| `fragments.py` | Legal break units, source coverage, continuation placements, wrapper preservation. |
| `allocation.py` | Immutable region partitions and certified visible page combinations. |
| `joint_search.py` | Bounded enumeration, contextual certification, conservative pruning, retained results. |
| `composition_planner.py` | Public target selection, scene assembly, diagnostics, one DEBUG exhaustion log. |
| `binding_preparation.py` | Frontend handler and form preparation, without registration or publication. |

`RealizationOffer.flow` exposes legal child boundaries. `FragmentContract` exposes ordered leaf
units, legal ends, repeated source references, and a constructor for a contiguous payload.
`RegionContract` supplies page chrome. `FragmentCoverage` separates source anchors from placement
identity. Tables expose row boundaries and repeat column headings without changing presentation.
`unbreakable` suppresses internal breaks; `keep_with_next` propagates through flow wrappers.

`OfferDiscovery` distinguishes a complete domain, unsupported vocabulary, and interrupted discovery.
Existing nonempty tuple-returning rules declare a complete domain; their empty tuple means unsupported.
A complete empty `OfferDiscovery` means no choices, rather than unsupported vocabulary. Extensions
must charge their own discovery, preparation, construction, and validation through `context.work`.

`evaluate_composition` retains the phase 2 preflight, fidelity, interaction, atomic, and structural
resource checks. `construct_composition` builds admitted preparation and independently validates it.
Fixed and automatic composition share both functions. The solver never inspects a native scene body.

## Resolved comparison policy

The step 1 checkpoints are now implemented with these rules:

1. Hard resource limits, structural constraints, permissions, and strictness determine admissibility.
2. Compare soft allocation minimum shortfalls before presentation preferences. Within each resource
   axis, compare the largest shortfall first. Axes use the declared `Axis` order; quantities on
   different axes are never added. An inactive conditional region has zero shortfall.
3. Normalize distinct preferences within each offer group before filtering feasibility. Within a
   region, compare active ranks worst-first with missing entries treated as zero. Its own choices
   precede its nested region groups. Equal-priority siblings compare their worst region first;
   higher `RegionPolicy.priority` tiers precede lower tiers. Extra satisfied conditional choices
   are neutral; extra dissatisfied choices can worsen an allocation.
4. For tied preferences, lossless dominates lossy; a strict subset of the same source-scoped effects
   dominates its superset. Unrelated losses remain incomparable. Retain all nondominated outcomes.
5. Within comparable fidelity, minimize violated `min_fill` and `widows` goals, then total page turns.
   Count each logical page once, even across many sibling combinations. A violation in any reachable
   context counts. This compares satisfaction without mixing character and item shortfall magnitudes.
6. Resolve equivalent results by occurrence paths, offer keys, and cuts. Select deterministically from
   incomparable results without claiming one preserves more meaning.

`RegionPolicy.goals` contains `AllocationGoal(axis, minimum, hard=False)`. A hard goal reserves
unused capacity and participates in enclosing structural and global limits. Existing
`sl.budget(min=..., prefer=..., stretch=...)` retains its hard character floor. With zero stretch,
its preferred grant caps body characters. `Paged.chars` remains a body cap; navigation is measured
separately in the enclosing composition. Nested floors reserve their maximum requirement, rather
than double reserving overlapping space. Every nonempty region still needs a complete legal unit;
no additional arbitrary default character minimum is invented.

The tests cover ordinal gaps, reordered offers, second/second fairness, explicit priorities,
nested and conditional choices, strict and non-strict loss, and incomparable effects. The recursive
fairness policy intentionally does not offer richer first/third versus second/second tradeoff controls.

## Search and completeness

A deterministic best-first queue holds partial offer assignments and legal partition states.
Conditional children are discovered only when their branch is visited. Balanced refinements run
first as probes to find a usable allocation early. They only affect work order: every legal cut
remains in the supported domain. Partition states are deduplicated only within the exact same
representation assignment. Opaque payloads and equal scalar costs are never used for dominance.

The initial sound bound discards refinements of a feasible family whose soft goals are all satisfied.
Those refinements preserve its choices and effects and necessarily add page turns. Otherwise search
retains the partitions. It does not assume monotonic target costs or prune using standalone costs.
The bound and queue are checked against independently enumerated small domains.

Each candidate certifies all reachable visible sibling combinations. Nested regions contribute only
on parent pages containing them. Resource sharing is evaluated in each actual visible scope, rather
than using a union or scalar maximum as proof of infeasibility. Conflicting values for one shared
identity reject their coexistence; they may appear on separate pages. Construction mismatches remain
fatal invariant errors, even when an earlier valid allocation exists.

Every reachable page is built, independently validated, and assembled, including frontend bindings
and form preparation, before its family can become an incumbent. This is stronger than building
only the requested page: cursor coordinates cannot affect search work or the returned partition.
Page selection requires no new target evaluation, construction, or handler preparation.

Completeness requires finite declared offers and legal ends, complete supported discovery, and enough
work to drain the queue. Optimality means nondominance under the comparison above, within that domain.
It does not mean that no imaginable rendering could be better. The larger default-allowance fixture
establishes a usable certified result; it does not require exhaustive completion for large domains.
Cross-request reuse and stronger bounds remain phase 5 work.

## Work and outcomes

Automatic composition uses `CompositionOptions.work_limit`, defaulting to 50,000 declared operations.
This is one allowance from semantic traversal through result assembly, shared with target operations.
Fixed composition retains `PlanRequest.search_budget`. Work units bound declared operations, rather
than wall-clock time spent in arbitrary custom Python or callbacks.

`PlanReport.search` contains completion, optimality, work used and allowed, violations, and unsupported
occurrences. `PlanMetrics` separates `work_used`, `generated_states`, `target_evaluations`, and
`certified_combinations`; the legacy `states_explored` measurement counter is not repurposed.

- Completed search returns a certified allocation with established optimality over the supported domain.
- Exhaustion with an incumbent returns that allocation, with `completion=EXHAUSTED` and `optimal=False`.
- Exhaustion without an incumbent raises `JointExhaustedError` with structured diagnostics.
- Completed infeasibility raises `JointInfeasibleError`.
- Unsupported vocabulary or resource axes raises `JointUnsupportedError` if no allocation is available.
  A result found while another branch is unsupported carries `completion=UNSUPPORTED`, without optimality.

Exhaustion logs once at DEBUG at the public boundary. The returned layout has no user-facing warning.
Fault injection covers discovery, evaluation, construction, validation, and finalization. It verifies
that partially certified families never replace an earlier validated result. Neither planning nor
preparing discarded bindings invokes application handlers or publishes presentation state.

## Public use

```python
from squid_ui.planning import CompositionOptions, OccurrenceId, plan

result = plan(
    document,
    target=target,
    composition=CompositionOptions(automatic=True),
)

# Zero-based explicit coordinates; no new allocation or target work.
next_page = result.for_page({OccurrenceId(("root", "results")): 1})
```

`CompositionOptions.pages` selects the initial output. Unknown region paths are rejected; indexes
clamp to valid extents, and coordinates for currently hidden nested regions are ignored.
`selections` pins offer keys, `partitions` pins exclusive legal page ends, and `policies` supplies
region priorities and goals. All mappings are copied into immutable views.

`result.allocation.regions` exposes each region's key, cuts, source anchors, measured allocation,
and typed `NavigationInput` records for each page. `result.for_page` retains the same allocation,
diagnostics, and metrics while selecting the visible scene, resources, bindings, and forms.
Allocation identity is a request-local token, stable for this allocation's lifetime. It deliberately
is not a content fingerprint or a cross-request cache key.

## Validation and phase 4 handoff

Focused tests include `test_allocation_policy.py`, `test_composition_fragments.py`,
`test_joint_search.py`, `test_joint_search_budget.py`, and `test_automatic_composition.py`.
They compare feasible sets and selected optima with exhaustive preference, partition, multi-axis,
and shared-resource fixtures; check incomparable nondominated effects; exercise all three targets;
and cover wrapper constraints, repeated chrome, nested visibility, hard and soft goals, stable pages,
source anchors, exhaustion, reservations, and page-local bindings. Existing composition, resource,
semantic correspondence, UI, and Discord region tests remain regression evidence.

Phase 4 should retain the certified allocation for cursor-only movement, use source anchors to
reconcile changed content, bind its navigation inputs to the authored ownership policy, and stage
state changes for successful delivery. A changed document, target, reservation, localization, chrome,
policy, or live resource requires a new allocation. Hidden coordinates need not be discarded, but
hidden controls and resources must not be published. Failed delivery must retain the prior committed
allocation and bindings.

The automatic path does not consume mounted `positions`, external `nav`, or document-level assets.
Region ownership is not reconciled with session state yet. Download nodes in supported content work.
Character-specific `Paged`/`Budgeted` vocabulary requires a measured character axis; Slack and HTML
report it unsupported rather than guessing a conversion. HTML regions and explicit row fragmentation
work without inventing a global resource budget. The broader widget and target matrix remains phase 6.

| Remaining production route | Removal or integration phase |
| --- | --- |
| `planning/regions.py`, mounted region allocation and staged updates | Phase 4 |
| Legacy root and local pagination, controlled/uncontrolled/routed ownership | Phase 4 |
| `PlanCache`/`PlanMemo`, fingerprints, cross-request reuse | Phase 5 |
| Classic Discord, Slack modal/home, and unsupported composition widgets | Phase 6 |
| Legacy complete backend discovery and target-specific fallbacks | Phase 6 after coverage and caller migration |

All five implementation steps are complete. This finishes phase 3, not the entire pagination migration.
