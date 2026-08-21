# 21 — Cursor sources: `PositionPolicy` and `WindowSource`

## Problem

- `PageBroker.grant` consumes a total page count, a fingerprint of *all* content, and
  an anchors map for everything — the materialized world's luxuries. A keyset/cursor
  source has none of them; an offset+COUNT source has some.
- Planning over 200k builds must not require materializing 200k items.
- The precedence ladder (override > anchor > stale-reset > stored > initial) mentions
  totals only in clamping. The policy is the valuable part, and it is accidentally
  expressed in page indexes.
- A pattern hand-rolling async paging would fork that policy: two pagination brains
  with different reset semantics, which plan 06 exists to prevent.

## Design

> One position policy, many sources; a source declares what it can know, and the
> chrome says no more than that.

1. **Extract `PositionPolicy`**: the precedence ladder as a pure function over an
   abstract position `{anchor key, offset, direction}`. The broker becomes its first
   caller — existing slicers derive index/pages from positions, behavior unchanged;
   this is the refactor plan 06 arguably owed. Patterns are its second caller, so
   there is one pagination brain even while there are two fetch paths.

2. **`WindowSource` protocol**: capability flags `countable`, `bidirectional`,
   `jumpable`; `async fetch(position, extent) -> Window(items, has_prev, has_next,
   total | None)`.

3. **Chrome degrades by capability**, through the existing nav-factory
   parameterization: "Page 4 / 9" → "31–40 of ~2000" → "31–40" → plain older/newer. A
   source that cannot count never shows a count — honest, not degraded.

4. **Staleness on window terms.** Fingerprint only the visible window. A mismatch's
   fallback is source-defined: re-fetch from the anchor; if the anchor item is gone,
   the source picks (nearest key, newest, start). This extends `cursors.py`'s stated
   anchor-outranks-reset philosophy; reset-to-start remains the materialized-list
   policy, not the universal one.

5. **Fetch stays out of planning.** Now: pattern-level — the prev/next handlers are
   already async; they fetch, write `{window, position}` into component state, and
   invalidate, with a per-key monotonic token dropping out-of-order results. Later: a
   core load phase that re-runs when a declared input changes — which is the
   dependency model whose absence cut `sl.resource` from plan 09. The two are the
   same missing design and must be designed together or not at all.

6. **Overrides generalize.** The explicit `page=N` map becomes position tokens — also
   the two-shell rule's stateless entry (plan 19): a routed panel carries its position
   in the custom id and passes it as an override, having no session to consult.

## Resolved API

- `Position(anchor, offset, direction)` is the portable token. `direction` is
  `"around"`, `"forward"`, or `"backward"`: refresh around an inclusive anchor, fetch
  after the trailing anchor, or fetch before the leading anchor. `offset` is a
  zero-based hint and becomes authoritative only for a jumpable source.
- `PositionPolicy.resolve(...)` accepts an override, a resolved anchor position, the
  stale bit, stored and initial positions, a reset position, and an optional upper
  bound. It has no session or source dependency. `PageBroker` projects its integer
  cursors into these inputs and projects the result back to a page index.
- `Window(items, has_prev, has_next, total, position)` may return a corrected position
  after an anchor-gone fallback. `WindowSource` declares `countable`,
  `bidirectional`, and `jumpable` and implements `fetch(position, extent)`.
- `WindowCursor` owns source reconciliation outside planning. It fingerprints only the
  returned item identities, refreshes around the visible anchor, and uses a monotonic
  request token so only the newest fetch may publish. Sources therefore own the choice
  of nearest key, newest item, or start when an anchor disappears.
- `RankedList(..., source=...)` is the first async consumer. Its component shell loads
  the first window in `on_load`; navigation handlers fetch and invalidate. Async
  sources are intentionally unavailable through `RouterShell`, whose route handler
  must fetch before rendering a materialized ranking.
- Countable+jumpable sources use the existing page footer. Countable sequential
  sources show a range plus approximate total, jumpable uncountable sources show only
  a range, and sources with neither capability show no numeric footer. Previous is
  omitted for forward-only sources and every control follows the returned `has_*`
  flags.

## Verification

- The `PositionPolicy` extraction is behavior-preserving over the existing pagination
  suite (same grants for the same sessions).
- Window-scoped fingerprint reconciliation; anchor-gone fallback per source.
- An out-of-order fetch result is dropped.
- Capability-gated chrome: an uncountable source never renders a page count.

## Status

Agreed 2026-08-21 (extract now); not started.
