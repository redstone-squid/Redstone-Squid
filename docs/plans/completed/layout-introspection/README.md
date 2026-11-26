# Contextual content, pagination, and layout facts

Implemented for bounded, materialized content: scoped layout facts, contextual
generation, and delivery-driven visibility. Source item counts and declared groups
are separate from rendered page counts. Flow across lazy source batches was dropped;
see the last section.

The rule is to certify the interface being delivered and state what is known for
that capture. Conditional rendering does not make every total unknowable: a fully
materialized document can have an exact total now and a different total after navigation,
hydration, or a state change.

## Distinct units

| Object | Meaning |
| --- | --- |
| Collection | Ordered application items, possibly changing or incompletely loaded |
| Batch | Items returned by one source fetch, with source-defined positions and neighbours |
| Group | An explicitly declared division of items, independent of target fit |
| Flow | Authored content and legal continuation boundaries within a named scope |
| Page | A bounded realization of part of a flow under particular layout inputs |
| View | Selected region fragments and their surrounding content |
| Delivery surface | A message, continuation message, or browser surface |

A fetched batch does not promise to fit. Ten items can require several rendered
pages. Message continuations are delivery surfaces, not collection positions.

`sl.bullets(..., page_size=10)` limits items per fragment and permits smaller cuts.
`group_size=10` declares fixed groups: the planner neither splits nor merges them.
An oversized group must adapt through an authored alternative or fail certification.
The empty flow has zero content pages and a known-absent selection.

`sl.region(..., page_size=N)` caps breakable content units; an unbreakable row
counts as one unit. Screens can declare all control windows and leave their selection
to the runtime. `InitialPage(anchor=sl.element_ref(NodeType, "key"))` chooses where a region first opens.
Actions use `event.reveal(sl.element_ref(NodeType, "key"))` to navigate again, without
storing request tokens. References resolve in the interaction placement and its ancestors.
Requests survive failed delivery and are consumed after successful delivery; action rollback
discards them. Later requests win when destinations conflict within one region.

For lazy sources, `SourceCapabilities.fixed_groups` explicitly promises aligned,
complete groups and consecutive neighbour positions. `list_source(...,
fixed_groups=True)` provides this contract for sequences. Group count and target fit
are separate obligations. Generic source footers report item ranges; only declared
groups support numbered source groups. `NavigationState.unit` distinguishes items,
groups, and rendered pages.

## Contextual API

```python
results = sl.region_ref("results")


def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
    region = ctx.region(results)
    selection = region.selection.require()
    if selection is None:
        return sl.paragraph("No results")
    number = selection.number
    total = region.page_count
    if isinstance(number, sl.Known) and isinstance(total, sl.Known):
        return sl.paragraph(f"Page {number.value} of {total.value}")
    return sl.paragraph("More results")


document = sl.stack(
    sl.bullets(*items, key="results", page_size=10),
    sl.context(footer, key="footer"),
)
```

`sl.context(callback, *, key, initial=(), components=())` returns ordinary semantic
content. Its generic parameters preserve target and event-context types; the actual
signatures are exercised in `packages/squid-ui/tests/typing_contextual.py`.

Callbacks can run repeatedly. Capture state, presentation values, inherited inputs,
and loaded data during `render()`, then close over those values. Callbacks cannot
sample live reactive state, write it, construct components, or perform I/O. Queries do
not start loading. Arbitrary application objects must remain unchanged during an attempt;
the runtime cannot preempt an infinite synchronous Python callback.

Built-in pagination chrome is also a `sl.context` declaration. Certification evaluates
it for each candidate view before measuring the target, using the same effect guard
and fact types as authored callbacks. This adds no outer feedback rounds for ordinary
pagination. `ctx.region().navigation` exposes available movements and their bindings;
its ordinal, extent, and jump availability follow established facts. Static and mounted
targets use the same semantic controls.

Custom `Paged.footer` and `Paginate.footer` callbacks receive one `LayoutContext` and
return text. The old `(page, pages)` callback is removed. `Chrome.page_footer` remains
a text formatter for already established numbers, also used for declared source groups.

Generated content participates in the actual target budgets, including footers,
controls, bindings, and assets. The resolver requires stable structure and repeated
observations, detects cycles, and bounds rounds and shared planning work. A rejected
branch can use an explicitly authored fallback. Exhaustion and unavailable required
facts fail without publishing provisional output.

## Scoped knowledge and queries

`Known[T]` and `Unknown(reason, detail)` are distinct answers. `.require()` returns a
known value or raises `FactUnavailableError`; an unresolved required read prevents
certification. `Known(None)` means established absence, rather than unknown.

A region exposes `page_count`, `selection`, `group_count`, and `navigation`. `page_count` is the rendered total
for this capture; `group_count` is an authored fixed-group count. Neither claims knowledge
of unseen source items. `PageCoordinate.number` is a fact rather than a magic integer.
`Locations.fragments` carries fragment and repetition provenance, page coordinates,
and visibility; `complete=False` leaves unexamined content unclaimed.

Contextual queries use canonical semantic occurrences, counting an item once even
when it splits into several fragments:

| Scope | Coverage |
| --- | --- |
| `ctx.authored` | Captured authored declarations and prepared slots |
| `ctx.placed` | Content represented by the current resolved plan |
| `ctx.visible` | Content represented in the selected view |

`ctx.locate(match_or_element_ref, within=region_ref)` resolves known fragments.
References inside a component resolve relative to its placement. Ambiguous references
fail. Matches are pinned to their capture; they do not silently retarget another tree.
Atomic target payloads remain opaque.

`plan(..., inspect_layout=True)` exposes these facts without authored contextual generation.
Documents with authored contexts expose them automatically through `PlanResult.layout`. Geometric
facts and automatic loading are not part of this API.

An authored contextual selection read requires replanning on navigation, but does not by itself
make the current total unknown. Fully known current content can retain exact counts
and complete locations. If only the demanded view can be certified, totals become
unknown, locations are partial, and numeric total/jump chrome is suppressed.
This view-scoped fallback also applies to built-in pagination without an authored context.
Selecting a later fragment does not establish its rendered ordinal when preceding
fragments have not been certified; its authored group position remains a separate unit.
`CertificateScope.VIEW` distinguishes this proof from a complete family certificate;
view-dependent callbacks also require view-scoped replanning even when current facts
are complete. A subsequent view can legitimately have different cuts or totals.

## Components, visibility, and publication

Declare keyed component slots before planning:

```python
editor = self.editor.keyed("editor")
shown = self.shown
return sl.context(
    lambda ctx: editor if shown else (),
    key="conditional-editor",
    components=(editor,),
)
```

Slots prepare eagerly, concurrently where independent, once per shared instance.
`on_load()` prepares the first useful state, not an entire remote collection.
Preparation diagnostics record declaring paths, elapsed time, outcome, and reuse.
Optional enrichment belongs in a resource with useful initial content.

Committed declarations retain logical lifetime, state, presentation cells, and services
while omitted. Synchronous `on_show(placement)` and `on_hide(placement)` report presence
in delivered fragments. Shared instances receive one mount and per-placement visibility
hooks. Hide precedes eventual unmount. These hooks describe delivered presence, not
viewport intersection.

Discord publication commits resolved queries, selected facts, bindings, cursor state,
and visibility after successful delivery. Discarded or failed candidates do not advance
them. Page-only changes can update visibility without changing the logical tree.
Live queries follow committed generated content; `.capture()` retains earlier membership.

## Lazy flow across batches: dropped

A forward-only `SourceFlow` let one page cross fetch batches and split an item at
paragraph boundaries. The general version would have needed continuation addresses
inside items, source revisions, Previous through visited checkpoints, and reflow
anchoring. No screen needed it, so the widget, `SourcePage`, `SourceStability`,
`Batch.revision`, and `ContentAddress` were removed.

Bot content is ours, so it can always be paginated into fixed pages: one batch is one
page. A page that does not fit continues in further messages, then splits or shortens.
Predictable pages are better UX than a flow whose page breaks the reader cannot
anticipate. Redesign from scratch if a screen ever reads content we cannot paginate.

## Validation

Production typing probes live in `packages/squid-ui/tests/typing_contextual.py`.
Planner tests cover scoped queries, fragmented locations, feedback, and partial facts;
Discord integration tests cover delivered visibility, generated handlers, and failed
publication. Pagination chrome tests exercise the same contextual evaluation directly,
including custom footer queries, unavailable counts, and effect guards.
