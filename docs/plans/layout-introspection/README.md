# Contextual content, pagination, and layout facts

This revision replaces the earlier requirement to construct and certify every page
before contextual content can be used. It proposes a model for discussion, not a
finished public API. Runtime pagination has not changed. Live selector roots,
explicit captures, and eager component-preparation diagnostics are implemented.

The central rule is: certify the interface being delivered, and state the scope of
what is known. A layout can be consistent now without knowing every future page or
remaining unchanged after more content loads.

## What a page means

Separate the objects that the existing UI sometimes presents as one page:

| Object | Meaning | Owner |
| --- | --- | --- |
| Collection | Ordered application items, possibly changing or incompletely loaded | Source |
| Window | A fetched slice with source-defined positions and neighbours | Loader |
| Flow | Authored content and legal continuation boundaries within a named scope | Authoring/runtime |
| Page | A bounded realization of part of a flow under particular layout inputs | Planner |
| View | The selected combination of region fragments and surrounding content | Presentation/runtime |
| Delivery surface | A message, continuation message, browser surface, or other target container | Frontend |

A source window is neither a promise to fit nor a mandatory layout boundary. A
window can require several pages; a page can use content from several windows if
loading policy permits it. An authored batch of ten items can deliberately define
its own scope, but ten items are not a target-fit guarantee. One message can contain
several independently paged regions. Message continuations are delivery surfaces,
not automatically positions in an application's collection.

A page is a layout result, not a durable integer identity. Its description includes
the flow, the covered content boundaries, the relevant input revision, the target
constraints, and its contextual expansion. Text can split inside an item, so a
position sometimes needs an intra-item continuation boundary as well as an item key.
Such boundaries must be declared by the content/target protocol, not guessed from
Python object identity or invented Discord pixel coordinates.

Reserve an internal page identity for that realized result. Navigation retains a
content address and enough revision information to validate it. A page's ordinal is
a derived fact about a particular pagination, not its address. The same address may
appear on a different numbered page after reflow.

## Navigation and numbering

Next continues after the last consumed content boundary, including any unconsumed
content already loaded. Crossing a fetch boundary does not skip that remainder.
Previous can use a valid visited checkpoint; discovering an unvisited predecessor
requires an appropriate source/layout capability. Forward-only sources must not
acquire arbitrary backward seeking merely because the UI has a Previous button.

A continuation records the flow/source identity, a source position and possible
intra-item boundary, and validation information for the relevant revisions and
layout inputs. Exact representation and persistence rules need a typing pass.
Every successful Next step must consume content or reach a declared end. A loading
state is not a content page or a zero-progress continuation. An underfilled view may
be delivered according to policy; filling it across windows requires explicit, bounded
lookahead demand outside the solver. More-content availability may itself be unknown.

Navigation requests are relative to a delivered revision. Fetching, loading, or
planning a destination does not advance the committed cursor. Delivery commits the
new content address, facts, and visibility together; failure preserves the old UI.

A page number is known when the preceding boundaries in the same pagination are
established, for example by resolving a contiguous prefix from the start. A direct
seek to an item does not establish how many rendered pages precede it. Counting
button presses is not a page ordinal. Source offsets remain useful independent facts.

An exact total requires a closed enumeration or another sound proof under declared
constraints. Knowing 100 items or ten source windows does not prove ten layout pages.
Discovering an exact total can itself change contextual output and invalidate earlier
boundaries or ordinals. Revalidate those dependencies rather than preserving old numbers.
Estimates and bounds must be labelled. A finite fully materialized family can provide
an exact total, but exhaustive enumeration is an optional capability/optimization.
A known exact count is valid only for its captured scope and revisions.

For an incomplete flow, useful copy is "Items 41–50 of 100", "More results", or
"Page 3" when its prefix is established. "Page 3 of 20" requires both facts. An
empty flow should have zero content pages; its region can still display an empty
state. Do not manufacture "Page 1 of 0" to make a pager's integer API convenient.
This empty-flow convention is a proposed behavior change to review with the API.

## Content depends on a context

Pagination is one consumer of a general contextual-content boundary. Keep the
proposed `sl.context(callback, key=..., initial=..., components=...)`: a callback
receives typed facts and returns ordinary target/context-preserving layout content.
The precise fact and continuation types below are not yet settled signatures.

Distinguish three dependency mechanisms:

- Captured inputs: state, presentation values, inherited bindings, loaded data, and
  target capabilities. Reading these need not introduce layout feedback.
- External demand: a declared resource or navigation request loads required data
  asynchronously. A fact query does not itself fetch or start component lifecycle.
- Derived layout facts: visibility, chosen representation, fragmentation, available
  capacity, and locations. Generated content can change these and require iteration.

Ordinary components render against captured inputs. Contextual callbacks may run
repeatedly but must be pure: no I/O, reactive writes, mutable state machines, component
construction, or lifecycle effects. Capture definitions and values once per attempt;
async completions create a new attempt. Referenced application objects must not mutate
under a captured attempt. Budgets cannot preempt an infinite synchronous Python callback.

An eager component load prepares that instance's first useful state. It does not
materialize an entire lazy collection or establish all item sizes. A source can
remain open even when every declared component slot has finished `on_load()`.

## Facts have scope, availability, and evidence

A fact identifies its scope and relevant revisions: source/data, render snapshot,
target constraints, navigation position, and candidate/view as applicable. A cache
may omit an input only when tracked dependencies justify doing so. Facts from another
candidate, source revision, or delivered generation cannot silently fill a gap.

A mutable source may not offer a consistent collection snapshot at all. Do not combine
counts or locations from separate fetches into one global claim without a source revision
or another stated consistency contract. Its current window can still be laid out locally.

The API must distinguish these cases rather than encode all of them as `None`:

| Answer | Example | Meaning |
| --- | --- | --- |
| Known value | Three fragments in this captured plan | Established within its stated scope |
| Known absence | This declared region is hidden | Established negative answer |
| Unknown | The total requires unloaded content | A legitimate limitation of present knowledge |
| Partial result | Two known locations; the remaining flow is unexamined | Membership is useful but not exhaustive |
| Estimate or bound | Approximately 100 items, or at least four pages | Evidence weaker than an exact total |
| Provisional solver value | A bootstrap location used in an iteration | Internal working hypothesis, never a certified fact |
| Failure | Source load failed, invalid reference, or exhausted work | Diagnostic outcome, not unknown or zero |

An unavailable capability must also be distinguishable from a fact that could become
known after loading. Authors may render explicit content for unknown/partial answers.
A required read without sufficient evidence prevents that requested result from being
certified; diagnose or request its declared preparation work as appropriate. Do not
label external waiting as nonconvergence. Do not silently load the rest of a collection
because a footer asks for its exact page count.

The next typing pass must cover discriminated known/unknown answers, precision and
coverage, required reads, and narrowing in ordinary Python. Optional selection and
fact availability are separate dimensions: a hidden selection can be known absent,
while a selected page can exist with an unknown ordinal or total. Avoid a magical
integer that stands for all these states.

## Queries, references, and locations

Reuse typed selectors and canonical semantic values. `query(runtime.queries, selector)`
follows the committed component tree; `.capture()` pins membership and occurrence
identity. A capture does not deep-copy application objects or claim that an external
collection is fully loaded. `.count()` counts captured matches, not unseen source items.
Returned matches never retarget themselves.

Contextual queries are pinned to their candidate, scope, and view. Proposed scopes:

| Scope | Meaning |
| --- | --- |
| `authored` | Captured semantic content and declarations, before generated expansion/omission |
| `placed` | Sources represented in the realized plan, including generated content and any materialized offscreen fragments |
| `visible` | Sources represented in the selected view |

These are not automatic queries over an entire remote collection. Each scope names
its coverage; asking for all source-wide placements can produce partial/unknown facts.
The current `ui_children` traversal visits representation alternatives and cannot
itself define canonical semantic membership. Count logical occurrences once across
fragments; preserve separate placements of a shared Python object.

Stable element/region references are scoped to their producing placement. Unkeyed
source indices are not stable across renders. Matches belong to one capture; passing
another capture's match to `locate` fails. Generated occurrences need contextual
placement identity, including invocation identity where different views generate
different content. Source identity, generated occurrence identity, page identity,
and delivery generation must remain distinct.

Location results carry all known fragments, continuation boundaries, nesting/address
information, repetition provenance, and coverage. A complete empty result establishes
absence/omission within its scope; an empty partial result does not. Repeated table
headings must not become extra logical items. Ambiguity is an error within the stated
lookup scope; finding one match in an incomplete global search does not prove uniqueness.
Geometric facts require a target capability.

## Planning, feedback, and certification

Planning realizes a demanded view from captured data, a content address, constraints,
and declared contextual computations. Data preparation may yield another captured
attempt. There are no asynchronous pauses or hidden fetches inside synchronous layout.

1. Establish the demanded content scope and available source/capability facts.
2. Prepare declared required resources/slots, or capture an explicit loading state.
3. Explore representations, continuation boundaries, and contextual expansions under
   one shared allowance. Bootstrap content supplies a provisional working state.
4. Track fact reads. Rebuild affected graph/offer/partition work when output changes.
   Resolve actual feedback cycles within this attempt, with bounded iteration.
5. Certify the selected view against target limits and the facts its output asserts.
   Include parent/sibling budgets and navigation chrome: fitting a child alone is insufficient.
6. Stage the scene, continuation, fact capture, and lifecycle changes; commit after delivery.

A certificate states its scope. A local certificate does not promise that an unseen
next page fits, that every alternative view has been explored, or that a global search
is optimal. A complete finite-family certificate can offer those stronger guarantees
where established. Navigating to an uncertified view prepares and certifies that view
before replacing the current one. Retain/reuse certified views only while their inputs
and dependency evidence remain valid.

Source positions/capabilities and known captured input facts can often resolve
acyclic computations directly. Actual feedback includes a visible-item summary that
changes which items fit, or an exact page total that changes fragmentation. Different
sizes on different pages are ordinary inputs to layout, not automatic inconsistency.

For feedback, stable equality includes generated structure, relevant choices,
boundaries, and observed facts. Detect cycles and bound nonrepeating growth. Reject a
nonconverging candidate and try declared alternatives within the allowance; distinguish
exhaustion from proof of infeasibility. Never certify a contradictory provisional answer
just because iteration stopped. An unknown total explicitly handled by the author can
be a consistent final answer; a stale guessed exact total cannot.

Certification is about consistency within an attempt. Data hydration, source updates,
changed constraints, or a different navigation request can legitimately cause reflow.
A numeric page from an old generation must not silently select a different content range.

## Components, visibility, and tooling

Contextual output may choose keyed component slots declared before planning. Discover
and eagerly prepare those instances, concurrently where independent and once per shared
instance; capture subtrees before layout. Callbacks cannot introduce undeclared component
instances. Eager slot preparation does not require eager loading of all remote data.

Committed declarations own logical lifetime, state, presentation cells, history, and
services even when layout omits a slot. Proposed synchronous `on_show`/`on_hide` hooks
report per-placement visibility after delivery: first fragment appears/last disappears,
with hide before eventual unmount. Speculative candidates trigger neither hook. Visibility
is presence in the delivered interface, not viewport tracking.

Preparation diagnostics already record per-instance time, outcome, reuse, and declaring
paths, with concurrent batch time separately. Extend tooling with demand/source scope,
fact availability/evidence, local versus complete certificates, resolution rounds/work,
and contextual dependency chains. A missing total should explain whether content is
unloaded, a capability is absent, or feedback did not resolve. Keep this in developer tools.

## Implementation sequence and acceptance cases

Keep the live-query and preparation foundations. Before planner implementation, replace
the old page-count-only stubs with types for content addresses, realized pages, scoped
facts, partial locations, and certificate scope. Review the empty-flow convention and
navigation copy alongside those signatures; this document does not settle every public name.

Implement a bounded materialized flow and page footer first through the general context
boundary, then extend demand-driven realization across source windows. Treat existing
`PageFamily`/`AllocationSnapshot` as the complete finite case, not the universal model.
Audit `PagePosition`, `MaterializedCursorRequest`, source `NavigationState`, Browser,
source-ranked views, and continuation delivery against the distinct units above.

Acceptance cases must include variable-size lazy items; one window spanning pages; a
page crossing fetch windows; an item continuing within itself; an oversized unsplittable
item with an explicit adaptation/failure; empty and forward-only flows; partial locations;
known item count with unknown page total; direct seek with unknown ordinal; prefix-based
numbering; stale counts/checkpoints after source updates or reflow; nested shared budgets;
exact closed-flow totals; contextual feedback cycles; and failed navigation preserving
previously delivered facts, cursor, state, and visibility.

## Prior art and existing experiments

[CSS fragmentation](https://www.w3.org/TR/css-break-3/) separates a flow, its fragments,
and the containers they occupy. [Typst context](https://typst.app/docs/reference/context/)
and [queries](https://typst.app/docs/reference/introspection/query/) demonstrate contextual
content and repeated layout evaluation. Borrow those distinctions and explicit evaluation;
our lazy sources additionally require scoped knowledge and demand-driven preparation.

The existing `spikes/layout_api.pyi` and typing examples describe the superseded complete-
family proposal. They are historical experiments, not signatures for this revision.
`spikes/experiment.py` exercises bounded feedback in an isolated row model;
`spikes/test_experiment.py` also reads real finite allocator metadata for 83 items at
12 per page. Those experiments do not establish lazy-source, continuation, partial-fact,
lifecycle, or production search semantics. They remain useful fixtures for the closed case.
