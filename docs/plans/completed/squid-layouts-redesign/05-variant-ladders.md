# 05 — Variant ladders: one structural-fallback node

**Landed.** Kept for the reasoning, not as a task list.

## Problem

Two node types encoded the same idea — "here are alternate structures for this region; give
one up when components run short":

- `Fold(primary, fallback, priority)` — binary.
- `Choice(variants, priority)` with `Variant.requires` — N-way, capability-tagged.

The first draft of this plan proposed unifying onto `Choice`. Auditing the code against it,
three of its claims did not hold and two real defects were missing.

**Corrections to the first draft.**

1. *"budget pressure never steps a `Choice`"* — it did. The planner lowered
   `Choice((a, b, c))` into `Fold(a, Fold(b, c))`, right-nested through the fallback, and
   `_folds` only walked the *selected* branch, so the solver already stepped one rung per
   iteration. N-way budget ladders worked, via a rewrite whose correctness rested on an
   unstated coincidence between the walk order and the nesting direction.
2. *"keep `Fold`; most call sites read better with it"* — no author constructed `Fold`, and
   after plan 14 no author constructed `Choice` either. Both were internal shapes.
3. *"coordinate with plan 04's `Ladder` helper"* — plan 04 rejected that name and shipped
   `Condense`. The item was stale.

**What was actually wrong.**

- **Two IR shapes, six duplicated traversals** across `solve.py`, `planner.py` and
  `runtime/component.py`, plus the `Choice`→`Fold` lowering rewrite.
- **`solve()` silently corrupted a `Choice`.** `realize` had no arm for it, so it fell through
  `case _` into the realized tree, counted as one component and unrenderable.
- **A variant that lowered to more than one node raised.** `_lower_single` demanded exactly
  one, so `Fold`'s own docstring example — a button panel folding to a select — exploded as
  soon as the panel needed two rows.
- **`Choice` collided with the dominant meaning of "choice"**: `sl.Choice` is a select-menu
  option, with `Choices`, `ChoiceEvent`, `sl.choice()` and `sl.choices()` behind it.
- **Diagnostics were unattributable**: every rung emitted
  `"folded a priority N alternate under component pressure"` with `path='$'`.

## Design decisions

- **The node is `Variants`, holding `Variant` rungs.** Plural-of-element mirrors `Alt`/`Alts`
  on the text axis, `Variant` already existed with the right docstring, and it frees `choice`
  for the select-option meaning. `Ladder` was rejected for the reason plan 04 rejected it:
  "ladder" already names the *text* axis (`Alts.ladder`, `Alt.fallbacks`, `_step_ladders`),
  and reusing it on the component axis would make the word ambiguous across both.
- **`Fold` was deleted, not kept as sugar.** `Variants.of(a, b)` covers the readable binary
  form without a second shape in the `Node` union.
- **`Variant.nodes` is a tuple.** A rung may lower to several nodes (an `ActionGroup` becomes
  one `Row` per five buttons). Splicing is exact; wrapping in a `Panel` to satisfy
  `_lower_single` would invent the very container component the ladder exists to save.
- **`requires` is resolved once, by the planner**, which clears it on the lowered result.
  `solve()` documents that it ignores `requires` and sees a pure budget ladder. Giving
  `solve()` a `capabilities` parameter was rejected: it would duplicate `TargetProfile`'s job
  into a layer with no concept of a target.
- **Stepping is breadth-first within a priority.** Only expressible once rung positions are
  explicit, and invisible to every pre-existing test because every ladder in the tree then had
  two rungs.

## What changed

- `primitives/nodes.py`: `Variant.nodes: tuple[Node, ...]`; new `Variants` with
  `Variants.of(*rungs, priority=...)`; `Fold` and `Choice` gone from the `Node` union.
- `planning/solve.py`: `collapsed: set[_FoldPath]` → `positions: dict[_VariantPath, int]`.
  Paths embed the selected rung index, so stepping a ladder abandons its descendants'
  positions rather than reinterpreting them against a different subtree. `_folds`/
  `_resolve_folds` → `_steppable`/`_resolve_variants` over a shared `_walk_ladders`;
  resolution returns a list per node so a multi-node rung splices. Selection keys on
  `(priority, current rung)`; `min` is stable, so full ties still fall to document order.
  `realize` now raises on an unresolved `Variants` instead of falling through.
- `planning/planner.py`: one lowering arm replaces two; `_lower_single` deleted; `_validate`
  walks every rung (so two rungs still cannot share a `Paginate` key, as under `Fold`).
- `runtime/component.py`: two pairs of traversal arms collapse to one each.
- `semantic.py` / `adaptation.py`: `FallbackContent.alternates` is a tuple and
  `sl.fallback(primary, *alternates)` is variadic, so ladders are expressible above the
  primitives layer. `adaptation._single`'s Panel-wrapping hack is gone with it.
- Notes now read
  `$.0 stepped to variant 2 of 3 (priority 0) under component pressure`.

## Out of scope

- `PlanEvent.path` stays `'$'`; structured event paths are a separate change at the
  note→event mapping.
- `solve()` still re-solves the whole document once per rung, bounded by the total rung count.
