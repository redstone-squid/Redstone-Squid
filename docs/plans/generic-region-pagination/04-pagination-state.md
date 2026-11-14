# Pagination and presentation state

Status: planned; implementation has not started. Phase 4 integrates the
[certified allocations from phase 3](03-joint-search.md) with mounted ownership and delivery.
The agreed scope includes recovery when a reading anchor disappears.

## Scope and compatibility

Make shared planning the default for supported root pagination, explicit regions, and
layout-driven local pagers. Keep explicit legacy routes for capabilities awaiting phase 6,
with the missing capability and removal phase recorded. Choose those routes by capability;
do not retry failed or exhausted shared search through an older solver.

Preserve authored constraints, content permissions, ownership, and reading position. Exact
historical page breaks, page counts, and representation choices are not compatibility
guarantees. Preserve `min_fill`, `widows`, hard budgets, grouping, and wrapper policies through
translation. Document intentional changes, including recovery instead of resetting immediately
when a remembered anchor disappears.

Keep data fetching and application-owned selection windows outside layout fragmentation.
Controlled and routed pages remain application-owned. Planning neither invokes change handlers
nor reads or writes their presentation cursors. Preserve negative ownership indexes, clamping,
explicit page overrides, and `RevealPage` intent handling.

## Current evidence

- `planning/planner.py` dispatches opt-in composition separately from mounted regions and
  complete backends. `composition_planner.py` rejects mounted positions, external navigation,
  and document-level assets.
- `planning/allocation.py` certifies every reachable page combination, but exported anchors
  describe only the first fragment unit of each page. Locating a surviving anchor after
  repagination needs coverage of the entire page.
- `planning/fragments.py` retains ordered units with source-reference sets. `SemanticGraph`
  retains authored child order; sets themselves cannot supply a reading order. Positional
  identities do not promise stability across insertion or reordering.
- `CursorCoordinator.grant` follows a surviving anchor and otherwise resets stale content to
  page zero. It already handles ownership, overrides, and reveal requests. It has no previous
  source order from which to recover a deleted anchor.
- `RegionNavigation` builds placeholder routes for previous/next controls. Mounted handlers,
  routed factories, custom navigation, and jump menus need integration before certification.
- `PresentationState` receives staged cursor and region-layout updates. Discord's candidate
  lifecycle publishes them after delivery. Existing region tests cover hidden cursors, failed
  delivery, sibling stability, and stale generations.

## Anchor recovery

Use the following implementation default for uncontrolled pages. Explicit navigation and a
new resolvable reveal intent keep their existing precedence over automatic reconciliation.

1. If the remembered anchor still has ordinary content coverage in this region's selected
   presentation, select its containing page, even when it is no longer the first item.
2. Otherwise, search the region's last committed source order for the nearest surviving
   anchor, measured by distance from the old anchor. Prefer the following anchor when
   distances tie, then select that anchor's page in the new allocation.
3. If no old anchor survives, or no usable history exists after a content change, select
   page zero. Unchanged content without an anchor retains its stored page; a new cursor
   uses its authored initial page.

For old order `a, b, c, d, e` with anchor `c`, deleting `c` selects `d` when both `b` and `d`
survive. If only `a` and `e` survive, select `e`. If only `b` survives, select `b`. Reordering
the new content changes the destination page, not which old neighbor is closest. Inserting
new items does not make them recovery candidates until a successful display records them.

An anchor identifies logical source content within one region occurrence. Keep that identity
separate from fragment placement, representation choice, and target control IDs. Preserve
existing public pager keys where unambiguous; reject collisions and document any necessary
key migration. Identical child keys in different regions must not share recovery history.

Retain ordered source identities and complete source-to-page coverage with the allocation.
Use authored semantic traversal to order sources within a multi-source unit; do not sort keys
or iterate a frozenset to infer reading order. Map alternatives back to their source identities.
If one source spans multiple pages, choose its earliest ordinary occurrence deterministically.
Repeated headings, footers, and navigation are not reading anchors. Nested regions own their
content history independently; a parent sees the nested region as one content unit.

Recovery candidates must remain represented in the new allocation. Omitted content cannot
receive the reader even if it remains in the semantic document. Unkeyed positional identities
offer no insertion or reorder guarantee; document that stable recovery requires stable keys.

Stage the recovered anchor and replacement history with the displayed cursor. Failed delivery
keeps the old anchor and history, so a retry makes the same recovery decision against the same
inputs. A region hidden by its parent retains its last displayed cursor and history until it
becomes visible again. Successful removal of a region collects both. Do not accumulate a history
of every render: retain only the source order needed for each remembered position.

Nearest-survivor recovery is preferred over retaining a numeric page because page numbers can
refer to unrelated content after insertion or new breaks. Resetting to page zero remains the
fallback when continuity cannot be established. Always choosing the next surviving item was
also considered; nearest distance avoids skipping a nearby predecessor for a distant successor.

## Ownership, navigation, and delivery

Adapt ownership to certified page coordinates through one shared coordinator. Keep public
negative indexes distinct from the low-level allocation API's clamped zero-based coordinates.
Do not let the representation selected for an occurrence change who owns its page.

Build and account for actual navigation during certification, including custom factories,
jump controls, localization, and routed IDs. Factories retain their repeat-safe contract;
planning may prepare discarded candidates but must not register controls or call handlers.
Preserve the mounted stale-generation policy and the routed host's responsibility for durable
state freshness. Only visible controls, forms, and resources may be published.

The mounted owner retains the committed certified allocation. Cursor-only movement selects
from it without search, changing sibling grants, or changing page boundaries. Changed content,
target, reservation, localization, chrome, policy, or live resources requires a new allocation.
Ownership changes must also reconcile cursor lifetime and bindings. Retained results must not
replay stale handlers or route encodings.

Stage the allocation, visible bindings, cursor reconciliation, and recovery history together.
Adopt them only after successful delivery; failed delivery preserves the previous committed
display. Distinguish a requested page turn from the committed displayed position. Retaining
one owner's allocation and invalidating it correctly belongs here; reusable fingerprints,
cross-request caches, diagnostics expansion, and performance tuning remain phase 5.

## Implementation sequence

Each step should leave a working, independently reviewable commit, with focused tests.

1. **Retain source order and coverage.** Extend allocation metadata with enough ordered source
   information to resolve any covered anchor and recover a missing one. Implement pure
   reconciliation and test deletion, insertion, reordering, alternatives, and legal groups.
2. **Connect ownership and navigation.** Adapt controlled, uncontrolled, routed, and reveal
   ownership to certified allocations. Include actual navigation in certification and support
   mounted inputs and document assets without bypassing resource checks.
3. **Integrate mounted explicit regions.** Retain committed allocations and recovery history,
   stage all presentation changes, preserve hidden region state, and verify delivery failures,
   retries, and stale input before switching this production route.
4. **Migrate root and local pagination.** Inventory each slicer and translate layout-driven
   pagination onto the shared machinery. Preserve wrapper policies and public ownership.
   Exercise supported V2, Slack message, and HTML paths; record concrete capabilities still
   requiring legacy routes. Classic Discord and Slack modal/home completion remain phase 6.
5. **Finish compatibility and handoff.** Remove superseded migrated dispatch paths, document
   changed behavior and remaining routes, update the ownership contract and roadmap, and
   record the invalidation and reuse requirements for phase 5.

## Validation and completion

Extend the allocation and cursor tests, plus Discord's `test_regions.py`, `test_pagination.py`,
`test_page_jump.py`, and `test_routed_pagination.py`. Add cross-target tests for ownership and
navigation where those capabilities are supported. Cover:

- A surviving anchor moving into the middle of a page; deleted anchors with nearest predecessor,
  nearest successor, ties, multiple deletions, complete replacement, and empty content.
- Reordering, changed page sizes, grouped units, alternate presentations, repeated chrome,
  duplicate keys in separate scopes, and explicitly limited unkeyed behavior.
- Hidden children through multiple parent updates, reveal intents, removal/reinsertion, and
  ownership changes. Controlled and routed pages must never acquire managed cursors.
- Negative page indexes, clamping, overrides, custom navigation, jump menus, and fresh routes.
- Failed delivery after recovery, retry, cancellation, and stale clicks. Assert committed
  allocation, bindings, cursor, and recovery history together, not only the displayed page.
- Cursor-only turns retaining the allocation and sibling partitions without new search;
  changed planning inputs invalidating it without reusing stale handlers or resources.
- Capability-based legacy routing, with no solver fallback after exhaustion or infeasibility.

Run focused tests with `--no-cov` during implementation and after the final relevant edit,
format and lint affected Python, run `git diff --check`, and run whole-project `just typecheck`.
Use installed commit hooks for their checks. Leave the full suite to CI unless integration
reveals broad or uncertain impact.

Phase 4 is complete when supported production pagination uses shared allocations, anchor
recovery and delivery behavior pass interaction tests, and remaining legacy routes have
explicit capability reasons and a phase 6 migration path. Writing this plan does not complete
the phase.
