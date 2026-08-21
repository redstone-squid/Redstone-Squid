# 06 — Pagination

## Problem

Pagination was implemented three times, in three layers:

1. **Solver `Paginate`** (`planning/solve.py`): text-budget paging of `Lines`/text units,
   pager-footer fixed point, late-realized nav validated at runtime by `_validated_nav`.
2. **Semantic option windows** (`planning/adaptation.py`): `Choices`/`Items`/`Navigation`
   and paged pickers rolled their own keyed 25-option windowing and emitted their own
   pager records.
3. **Root document pagination** (`planning/planner.py`): a third implementation, glued to
   the others only by precedence rules ("local and root are never simultaneous").

The first framing of this plan called that "three implementations of one concept" and
proposed compiling all three onto a single solver-level paged region. Reading the solver
properly says otherwise. The three slice for genuinely different reasons:

| | text pager | option window | root pager |
|---|---|---|---|
| Slice unit | characters | items, 25 to a page | whole components |
| Page count known | only after allocation | before anything runs | after probe solves |

Merging the slicers pushes a content-determined decision into a fit-determined engine.
It fails concretely on `Items`, whose chosen window feeds a `Lines` of summaries — the
solver would have to generate text from a window. And `_action_strategy` scores candidates
with `StrategyCandidate.active_pagers`, which is statically knowable *only because* option
windows are count-based; make windowing budget-derived and "does this strategy page?"
becomes a solver output, so `choose_strategy` would have to solve per candidate.

The real triplication was never the slicing. It was everything around it, and the three
copies disagreed:

- Three content fingerprints — item keys, fragment text, logical tree — behind one
  `ScenePager.content_fingerprint` field, so staleness meant something different per
  engine. `_paged_picker` wrote no fingerprint at all.
- Three page-footer policies: droppable, `Never`, and a worst-case reservation.
- Anchor recovery in `_page_items` was dead in production. The mount passed
  `page={key: cursor.index ...}` for every cursor, and the explicit override
  unconditionally beat the anchor adjustment. Its own fingerprint reset was guarded by
  `anchor is None`, and option-window cursors are always anchored, so that was dead too.
- The root packer cut a page whenever a probe produced *any* note, including cosmetic
  clamps, which are sticky across every probe containing the node that caused them. One
  misplaced `Paginate(per=...)` shredded a document into single-node pages.

And the mount rendered twice per flush — draw, compare fingerprints, reset stale cursors,
draw again — because a text pager's page count is an output of solving, so nothing
upstream could reconcile a cursor until the solve was done.

## Design

One cursor lifecycle, three slicers left alone, and the page index demoted to a
projection.

**06a — one cursor ladder.** `planning/cursors.py` holds a `CursorCoordinator`: a slicer asks
`grant(MaterializedCursorRequest)` where to cut, cuts, then `record`s what it did. The coordinator owns the
whole precedence ladder — explicit override, anchor recovery, stale-content reset, stored
position, `initial`, clamp — plus `controls()` (the `Never` footer and the nav) and the
`ScenePager` records with cross-engine duplicate-key detection. Anchor recovery outranks
the stale reset deliberately: if the item the reader was on still exists, following it
beats sending them back to page 1.

**06b — the page index is a projection.** `page` is read in exactly one place in the
solver, inside the tail that attaches pagers. Realization, allocation, pruning, the footer
fixed point and the ladder loop never look at it; every fragment fits the grant its unit
was allocated, the footer reservation is measured at its widest digit count, and
`navigation_controls` disables rather than hides. So `SolvedLayout.reposition()` moves a cursor by
rewriting two slots and replacing the span its nav occupies. Two contracts hold it up:
`NavNode` narrows what a factory may return (text in nav is now a type error), and shape
invariance across pages is checked in `reposition`.

**06c — planning reads, and returns its writes.** `PlanResult.session_updates` carries
cursor and strategy writes plus the set of keys still backed by a pager; the mount applies
them in `_commit`, after delivery lands. Rollback has nothing to undo, which retires plan
01's cursor snapshot and extends the guarantee to strategies and selections that snapshot
never covered. `plan()` solves once, reconciles every pager with the count in hand, and
repages — so the mount draws once. The mount stops laundering cursors through `page=`,
which makes anchor recovery reachable for the first time. A cache hit replays the writes
its miss would have made.

**06d — the root packer asks a real question.** `SolvedLayout.overflowed` separates
clamping (Discord's shape, enforced whatever the budget) from fitting (a degrade, spill,
drop, trim or ladder step). The packer cuts on the latter.

## Not done, and why

- **Compiling option windows onto a solver paged region.** Reasons above. The remaining
  asymmetry — an `Items` window is a text-budget input because of its summaries — is
  deferred; the lever if it ever matters is the existing count-page mechanism, where
  `_Unit.need` already budgets by the *widest* page.
- **Async source fetching in planning.** Fetching remains component-owned; planning stays
  synchronous. Plan 21 later unified source and materialized controls under one
  `NavigationContext` / `NavFactory` boundary without moving I/O into planning.
- **Memoizing the root packer's probes.** Every probe measures a different prefix and each
  is visited once, so there is nothing to memoize.
- **Plan-cache key refinement.** Dropping `page` and splitting `presentation` into
  layout-affecting and projection-only state would make page turns cache hits, but the
  cached artifact is a converted `SceneDocument`, so projecting after the cache needs a
  second representation. Follow-up.

## Constraints this had to respect

- `CursorState` stores a `Position`, extent, and fingerprint; snapshot protocol 1 serializes
  that shape directly.
- Materialized nav action keys use `__cursor_previous.{key}` and `__cursor_next.{key}`.

## Verification

- `test_pagination.py` gained `TestRepage` (projection keeps the component count, redraws
  nav, clamps, rejects a shape-varying factory), "the mount draws once per render", and
  the anchor divergence test on the production path.
- `test_planner.py` gained the root-fragmentation divergence test; it fails against the
  old `probe.notes` predicate.
- `test_mount.py`'s failed-edit test now also asserts strategies are unrolled.
- `test_cache.py` asserts a hit stages the same writes as a miss.
- Tests that plan directly and expect the session to move call `apply_updates` themselves,
  which is the stage→deliver→commit seam made explicit.
