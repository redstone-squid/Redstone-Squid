# Pagination and presentation state

Status: implemented. Phase 4 connects the [certified allocations from phase 3](03-joint-search.md)
to production pagination, mounted ownership, and delivery. It includes recovery when a reading
anchor disappears. Structural caching and broader target vocabulary remain phases 5 and 6.

## Production planning

`planning/pagination_migration.py` selects the route before search. Supported pagination uses
shared allocation by default on Components V2, Slack messages, and HTML. Supported content
includes paragraphs, lists, tables, downloads, checked alternatives, and the shared grouping
and budget wrappers. Explicit regions retain independent coordinates; nested regions are one
unit in their parent's allocation.

A keyed document with navigation and no local pager becomes a root region. Existing local
pagers keep their public keys. Components V2 also translates character-based `Paged` and keyed
primitive `Paginate` on text, code, lines, headings, and footers. Primitive text/code content
must be a string; lines may contain rich text. List numbering, authored `per` boundaries,
`min_fill`, `widows`, persistent chrome, grouping, and hard budgets participate in allocation.
Long text may have continuation fragments carrying the same source identity.

Compatibility means preserving content permissions, authored constraints, ownership, and
reading position. Historical page counts, exact breaks, and representation choices are not
guaranteed. Bounded search may return a certified allocation without proving it optimal.
The report distinguishes that result from infeasibility or exhaustion without a certificate.
No shared-search failure retries through a legacy solver.

Actual navigation is built before certification, including custom button rows, jump selects,
localized footers, routed IDs, and their resource costs. Discord uses the adapter's escaping
policy. HTML supplies its default jump navigation and Slack its default button navigation.
Document assets share attachment accounting with visible downloads and deduplicate by identity.
Only the selected scene's controls, forms, and resources are published.

The existing default search setting maps to a shared operation allowance of at least 50,000.
Mandatory count-based page combinations can raise that default, capped at 2,000,000. A nondefault
`search_budget` is an explicit shared operation limit. These are not the legacy solver's
measurement-call units. Balanced fit probes run before general cut expansion; deferred work
still retains every declared legal cut. Phase 5 should measure and refine these defaults.

## Reading position and recovery

Allocations retain complete ordinary source coverage for each page and source order from the
semantic graph. Source identity is separate from fragment placement, representation keys, and
wire control IDs. Grouped or alternate presentations can therefore locate an anchor that is
not the first source on a page. Repeated headers, footers, and navigation are not anchors.
Nested regions retain their own content history; their parent anchors the region itself.

For an uncontrolled page, explicit navigation and a new resolvable `RevealPage` request take
precedence. Otherwise:

1. Follow the remembered anchor if it remains represented in the allocation.
2. If it disappeared, find the nearest surviving anchor in the last committed source order.
   Prefer the following anchor when distances tie.
3. If no old anchor survives, reset changed content to page zero. Unchanged content without
   an anchor retains its page, and a new cursor uses its authored initial page.

With old order `a, b, c, d, e` and anchor `c`, deleting `c` selects `d` when both `b` and `d`
survive. If only `a` and `e` survive, select `e`. Reordering changes the survivor's destination
page, not which old neighbor is closest. Newly inserted items enter recovery history only
after successful display.

A source spanning several pages resolves to its first ordinary occurrence after content or
partition changes. An unchanged cursor already displaying a continuation stays on that page.
Stable recovery requires stable keys: generated positional identities are excluded from the
remembered anchor order. Duplicate identities in one scope are rejected; separate region
scopes keep independent histories.

## Ownership, reuse, and delivery

`CursorCoordinator` resolves uncontrolled, controlled, routed, and reveal ownership. Controlled
and routed pages use the application's value, including negative indexes and clamping, without
reading or writing presentation cursors. Planning never invokes their change handlers.
Selection windows and data fetching remain application responsibilities.

Shared navigation writes a pending page request rather than moving the committed cursor.
`present_composition` selects the certified coordinates and stages the cursor, source order,
region layout, and retained allocation. The frontend applies these updates after delivery.
Failed delivery or cancellation preserves the previous displayed state and bindings; retry
uses the same committed recovery history. Committing an older request cannot clear a newer
pending position. Hidden regions retain their last displayed history, and successful removal
collects their cursors and pending requests.

One mounted owner retains its committed allocation. `pagination_reuse.py` compares typed
semantic nodes explicitly, ignoring only a controlled owner's numeric value. It retains
callback, resource, policy, and container comparisons. Unknown extensions prevent reuse.
The request comparison also checks target, chrome, localization, palette, reservations,
navigation identity/version, search options, and presentation-session identity.

A cursor-only turn selects from this allocation without search or changing sibling partitions.
Changed callbacks rebuild bindings. Routed documents bypass retention because a route factory
can encode changed external state behind the same callable. Route and navigation factories
must remain safe to repeat for discarded candidates.

## Remaining capability routes

The following cases retain legacy planning until phase 6. `PlanReport.legacy_pagination` records
the first missing capability without reporting a content adaptation or degradation.

| Capability | Current route and phase 6 work |
| --- | --- |
| Classic Discord, Slack home and modal targets | Their complete backends; add shared target rules. |
| Semantic shells and controls outside the shared vocabulary, including sections, headings, media, fields, choices, and adaptive action groups | Existing target adaptation, or the V2 region adapter when regions are present; add realizations and resource contracts. |
| Primitive overflow alternatives, line degradation alternatives, and non-string primitive text fragmentation | Existing target slicers; expose their permitted alternatives and rich fragments to shared search. |
| Native primitive layout nodes, variant ladders, and extensions without a production translation | Existing target planning; add explicit shared capability contracts. |
| Character-specific `Paged` on targets without a character resource axis | Existing target behavior; define target-appropriate constraints before migration. |

Shared navigation currently accepts text/footer, button rows, links, buttons, and selects.
A custom navigation factory returning another primitive reports unsupported vocabulary; it does
not trigger a second planner. Backend caches remain available on the legacy routes.

## Validation and handoff

Tests cover complete coverage and source order, surviving and deleted anchors, forward ties,
reordering, empty replacement, hidden descendants, controlled changes, fresh callbacks,
negative indexes, overrides, reveal ownership, actual navigation costs, jump menus, and assets.
Mounted Discord tests cover failed and cancelled recovery delivery, retry, stale generations,
and retained allocation identity through page turns. Root and local migration tests verify
that exhausted shared search does not call the legacy region planner.

Both UI package suites were exercised. Focused regression checks cover pagination, target
composition, routed interactions, widgets, and reuse. Whole-project `just typecheck`, Ruff,
`git diff --check`, and installed commit hooks pass. Async file-delivery tests require running
outside this workspace sandbox: its thread wakeups hang even for a minimal `asyncio.to_thread`.

Phase 5 should integrate cross-request structural reuse, explicit reuse metrics, and bounded
preprocessing with the shared operation model. Retention currently compares and traverses
semantic inputs on a page turn; that is not another allocation search. Benchmark large text,
mandatory page combinations, and nested allocations before choosing cache or budget changes.
Keep typed semantic comparisons, current bindings, route freshness, and delivery-staged history
as invariants. Phase 6 can remove each legacy route after its missing capabilities are supplied.
