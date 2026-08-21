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

## Final API

This implementation deliberately breaks the transitional page-shaped API. The package
is still on its development branch, so preserving internal compatibility would make the
temporary bridge permanent.

There are no external consumers. Superseded public names and snapshot fields are removed
rather than deprecated or aliased; every in-repository caller moves in the same change.

- `Position(anchor, offset, direction)` is the only cursor token. `Direction` is an
  enum (`AROUND`, `FORWARD`, `BACKWARD`), and `CursorState` stores the position directly
  instead of splitting it into index and anchor fields.
- `PositionPolicy.resolve(...)` remains the pure precedence function. A
  `CursorCoordinator` adapts materialized slicers to it; slicers project the resolved
  offset only at their actual cut boundary.
- `SourceCapabilities(backward, offsets, jumpable, count)` replaces three unrelated
  booleans. `CountPrecision` is `NONE`, `APPROXIMATE`, or `EXACT`; validation rejects
  jump/count claims from a source that cannot know offsets.
- `Window(position, items, has_previous, has_next, total)` always returns its resolved
  position. Anchor-gone fallback is therefore explicit rather than hidden behind an
  optional correction.
- `WindowLoader` returns an immutable `LoadedWindow`. It owns only the monotonic request
  token; the component owns the loaded value as declared state. A stale completion can
  never mutate the visible cursor behind the component's back.
- `NavigationContext` and one `NavFactory` serve materialized and source windows. The
  mount injects its factory into components, so a custom navigation factory applies to
  both paths. The context carries proven boundaries, position, extent/range/total facts,
  labels, and handlers rather than assuming every cursor has a page count.
- `RankedList` returns to being a materialized, pure two-shell pattern.
  `SourceRankedList` is an explicit async component. Its distinct type makes loading,
  durability, and router limitations visible in the API rather than constructor modes.
- Numeric chrome derives from `SourceCapabilities`: exact+jumpable becomes a page
  count, known offsets plus a count become a range and total, offsets alone become a
  range, and keyset-only navigation has no numeric footer.

## Verification

- The `PositionPolicy` extraction is behavior-preserving over the existing pagination
  suite (same grants for the same sessions).
- Window-scoped fingerprint reconciliation; anchor-gone fallback per source.
- An out-of-order fetch result is dropped.
- Capability-gated chrome: an uncountable source never renders a page count.

## Status

Implemented 2026-08-21.

`test_pagination.py` keeps the materialized broker behavior under the extracted policy
and covers position-token overrides. `test_sources.py` covers window-scoped refresh,
source-selected anchor fallback, directional boundaries, and out-of-order completion.
`test_patterns.py` exercises source loading and navigation through a real mount and all
four countable/jumpable chrome combinations, including an uncountable source returning
a bogus total that never reaches the reader.
