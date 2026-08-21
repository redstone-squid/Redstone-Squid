# 06 — Pagination unification

## Problem

Pagination is implemented three times, in three layers:

1. **Solver `Paginate`** (`planning/solve.py`): text-budget paging of `Lines`/text units,
   pager-footer fixed point, late-realized nav validated at runtime by `_validated_nav`
   (solve.py:551-566, "component-bearing nodes only").
2. **Semantic option windows** (`planning/adaptation.py`: `_page_items` at 646,
   `_page_chrome` at 677, `_paged_picker` at 623): Choices/Items/Navigation/paged pickers
   roll their own keyed 25-option windowing and emit their own pager records.
3. **Root document pagination** (`planning/planner.py`: `_root_paginate` at 474+): a
   third implementation, glued to the others only by precedence/error rules
   (planner.py:378-391, "local and root are never simultaneous").

They are unified only by the `NavFactory` type and the `ScenePager` record. The Mount
carries the leftovers the planner could not own: the cursor store, `_move_page`, and the
double-draw dance (`discord/mount.py:214-228`) where it draws, inspects the resulting
pagers' content fingerprints, resets stale cursors, and draws again — the only feature
that makes the mount render twice, and the reason plan 01 needs a cursor snapshot.

## Design

Phased; each phase lands independently.

**06a — planner owns cursor reconciliation.** `plan()` already receives the
`PresentationSession` (threaded through `compose`). Move into `plan()`: fingerprint
comparison against session cursors, stale-cursor reset, page clamping, and cursor
anchoring — everything `build_view` does around its double draw. `plan()` returns a
scene whose pagers are final; the mount renders once and its pagination surface shrinks
to "store the session, forward page-move events" (`_move_page` stays). This deletes the
double draw and retires plan 01's cursor snapshot/restore workaround (delivery failure
then needs only candidate-tree discard; session writes happen during planning of the
delivered candidate — stage them alongside the candidate until commit).

**06b — one paged-region concept.** Introduce a single solver-level "keyed paged
region" (name: `PagedRegion` or extend `Paginate`) that the semantic option windows
compile to instead of reimplementing windowing: adaptation emits a paged region of
option-nodes with a per-page capacity of 25, and the solver produces the window, footer,
and nav exactly as it does for text pagers. `_page_items`/`_page_chrome` are deleted;
their identity/anchor logic moves into the shared region.

**06c — root paging on the same concept.** `_root_paginate` becomes "wrap top-level
structure in a paged region whose unit is whole components", keeping the existing
precedence rules (local wins; both-active is still a planning failure with remedies) as
planner policy rather than a parallel engine.

**Contract hardening.** Once nav realization lives in one place, express the
"component-bearing nodes only" nav contract in the `NavFactory` signature (a narrowed
node union) so `_validated_nav`'s runtime check becomes a type error instead.

Sequenced after plan 05 so the solver loop this reworks is already the merged-ladder
version.

## Verification

- Behavior-lock first: before touching code, capture current pager outputs
  (page counts, fingerprints, window membership) for the existing
  `test_pagination.py`, `test_navigation.py`, `test_planner.py`, `test_search.py`
  scenarios and assert the refactor reproduces them (except where a divergence is
  deliberate and documented in the commit).
- After 06a: `test_mount.py` gains "renders exactly once per flush" and
  "content change under a key resets only that cursor, decided in plan()".
- Full package suite after each phase; `just typecheck`.
- The sticky-strategy/hysteresis suites (`test_alts.py`, adapter strategy tests) run
  unchanged — presentation-state compatibility is a hard requirement, since durable
  snapshots serialize `CursorState`.
