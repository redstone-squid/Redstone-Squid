# Pagination ownership

> **Status:** implemented.

## Two kinds of pagination

`Paginate` is an overflow policy. Its page boundaries depend on layout, text budgets, and navigation chrome,
so the planner must resolve its requested page after measuring content. `Controlled(value, on_change)` reads
an application-owned page; `Uncontrolled(initial)` remembers it in `PresentationState`. `RoutedPage` supplies
an application-owned page with persistent navigation routes. Negative pages count from the end, and every
mode clamps to the pages that exist. Application-owned pages neither read nor write presentation cursors.

Option windows have different requirements. `MultiChoice` needs to know which choices are visible before
rendering so it can calculate selection limits and merge submissions. Its window is a snapshot derived from
machine state, not another reactive owner. `CollectionEditor` uses the same `OptionWindow` abstraction.

## Selection-window contract

The machine resolves a window after filtering unavailable options. `OptionWindow` owns slicing, clamping,
page counts, and shared footer/navigation construction. The machine keeps group exclusivity, global
cardinality, staged selections, and commit policy. Ordinary `Choices` still rejects cross-page multi-selection;
these widgets supply one resolved window instead of asking the planner to infer selection semantics.

Rendered selection actions carry a group, resolved page, and fingerprint of the ordered available keys and
window size. A delayed submission uses that rendered page even if the machine has since turned a page.
Changing membership, availability, order, or window size rejects the old submission without changing state.
Changing labels does not invalidate it. Direct programmatic selection actions still address the current window.

`MultiChoice` replaces only the submitted window's selectable keys. For example, with `a` selected on page
one and `b` on page two, submitting `c` from page two produces `a, c`; an empty submission leaves `a`.
A nonempty replacement clears exclusive rival groups. Menu capacity excludes those rivals, so a valid
replacement remains reachable when the current selection is at the global maximum. Invalid staged values
remain available for correction; explicit Apply and immediate commits retain their distinct behavior.
The small-panel modal continues to replace the whole selection.

Routed pickers now carry selected defaults into the planned scene. Both drivers therefore show the same
checked options. The route host still owns authorization and state freshness: a window fingerprint validates
option scope, not the currency of all selections stored in an old route. A host using shared durable state
must reject or reconcile stale state revisions before dispatching a transition.

The old `_paging.window` shim and `PagePosition` are removed. `PageAction` remains useful for widget page
turns. Moving option windows into the planner or making them use `Paginate` is not part of this design.

## Routed planner navigation

`Paginate(page=RoutedPage(value, route, seek_route))` is the explicit routed configuration. `route(target)`
encodes a button's clamped zero-based destination. The optional `seek_route()` encodes one input route for the
jump menu; option values are zero-based page strings. The host validates the submitted value and redraws.

`NavigationContext` carries callbacks or route IDs. The standard navigation factories produce `Button` and
`SelectMenu` for callbacks, or `RoutedButton` and `RoutedSelect` for routes. Custom factories must handle both
binding forms. Hosts choose their navigation factory; sessionless rendering also accepts `nav`, including
`page_select_nav` for jumps.

Route factories may run repeatedly, including for discarded layout candidates. They must be safe to repeat;
deterministic encoding without registration is preferred. Documents containing routed page factories bypass
structural and exact plan caches because those caches cannot identify changed route IDs hidden behind a
callback. This trades cache reuse for correct IDs on every render.

`RouteDriver.paged_lines` now hands the complete content to `Paginate`, just as the mounted driver does.
Both follow layout-derived page boundaries, including extra splits under text pressure. The routed testing
helper plans routed pagers and reports only routes reachable from the final scene. An explicit target also
allows other routed widgets to be tested through planning.

## Semantic APIs and validation

`semantic.Paged`, `sl.paged`, and the fluent `.paged()` accept `page: PageOwnership` instead of `initial`.
Semantic `List` and `sl.bullets` expose page ownership too. Numbering happens before Discord slicing; HTML
count-based pages preserve the corresponding item numbers. No `start` field is needed for this migration.
`paged_lines` remains Discord-only: its callers already supply formatted lines, and wrapping those in a
semantic list would add unwanted markers.

Tests cover selection across windows, stale submissions, availability filtering, clamping, exclusive-group
replacement at capacity, collection reordering/removal, and explicit/immediate commits. Planner and frontend
tests cover routed boundaries, jump dispatch after driver reconstruction, text-budget splits, fresh route IDs,
selected defaults, scene serialization, and controlled semantic pages without presentation cursor writes.
