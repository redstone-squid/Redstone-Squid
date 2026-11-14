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

The implementation sequence and technical search design follow the code investigation. Changes to
these agreed behaviors need discussion; choosing data structures and proving pruning rules do not.
