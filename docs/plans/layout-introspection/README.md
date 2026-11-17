# Layout introspection: design and typing spike

Contextual layout remains a design and experiment; pagination is unchanged. Live
selector roots, explicit query captures, and eager preparation diagnostics are now
implemented. The agreed
requirements are general contextual content, pure computations over a render
snapshot, region-relative pages, explicit target capabilities, and bounded
resolution that preserves the last delivered UI on failure.

The key references are [Typst context](https://typst.app/docs/reference/context/)
and [queries](https://typst.app/docs/reference/introspection/query/), LaTeX's
[label/reference machinery](https://latex-project.org/help/documentation/source2e.pdf),
and [CSS generated content](https://www.w3.org/TR/css-content-3/). Borrow stable
references, explicit deferred evaluation, and generated document content. Python
callbacks receive ordinary typed values once the contextual boundary is entered.

## Proposed authoring boundary

`sl.context(callback, key=..., initial=())` returns a layout node. `initial` is
bootstrap content, not a user-visible fallback. Its output preserves the callback's
render-target and interaction-context types. `ContextKey` lives in
`squid_ui.context_keys`, leaving the public `sl.context` name available.

```python
results = region_ref("results")


def footer(ctx: LayoutContext) -> sl.LayoutNode:
    pages = ctx.region(results)
    selected = pages.selection
    if selected is None:
        return sl.paragraph(f"{pages.count} pages; results are hidden")
    return sl.paragraph(f"Page {selected.number} of {pages.count}")


# Inside the ordinary component render:
sl.context(footer, key="results-footer")
```

A callback runs per contextual occurrence and reachable view coordinate. With no
explicit region, `ctx.region()` uses the containing region; an outside callback
must name one. Stored indices remain zero-based; `.number` is one-based display
numbering. A region hidden by its parent has a count but `selection=None`.

Application rendering captures data, presentation values, and callback definitions
once. Contextual callbacks consume that snapshot and layout facts, can return
layout content and controls, and may run repeatedly. They cannot perform I/O,
write reactive state, or start lifecycle work. Captured mutable objects must not
change during planning. A synchronous Python callback is trusted code: operation
budgets cannot preempt an infinite loop inside arbitrary application code.

`spikes/layout_api.pyi` gives concrete proposed signatures and
`spikes/typing_examples.py` exercises all three use cases against current layout,
selector, and Discord types. Neither file is a runtime implementation.

## Queries, identities, and absence

Reuse the existing selector language and typed matches. A live root query follows
the latest committed tree and registers reactive dependencies when read. An
explicit `.capture()` pins membership and layout facts for several consistent
operations; it does not deep-copy application objects. Prefer this single query
API over a `live=True` flag or unrelated functions. A returned match identifies
one occurrence and does not retarget itself when another element matches.

Layout evaluations pin queries to their candidate and coordinate. They never
read another candidate's latest answers. Use `query(runtime.queries, selector)` for committed live reads; `.capture()`
returns a `QuerySnapshot`. Raw trees retain their captured structural index.
Context exposes three explicit scopes:

| Scope | Meaning |
| --- | --- |
| `authored` | Captured canonical semantic content, before contextual expansion and candidate omissions |
| `placed` | Logical content represented anywhere in this candidate family, including contextual output |
| `visible` | Logical content represented in this coordinate's selected scene |

Queries return canonical source types through representation correspondence;
rendered target nodes require a separately advertised target capability. Reuse the
selector engine with an adapter for canonical semantic content. Its existing
`ui_children` traversal includes all alternatives, so it cannot directly define
these scopes. Counting a logical item counts it once even if it has several
fragments. Repeated instances of the same Python object remain separate matches.

Existing `Match` values belong to one capture. Passing an older capture's match to
`locate` fails. `element_ref(Kind, *keys)` provides a stable keyed locator scoped
to the producing component placement. Duplicate matches raise an ambiguity error;
callers can query a narrower scope or pass an exact match. Unkeyed source indices
are not stable references across renders. Generated keys are namespaced under the
contextual occurrence; coordinate-dependent expansions also need invocation
identity so different page outputs cannot alias one another.

`ctx.locate(target, within=region)` returns every fragment location. Each contains
an occurrence ID, a page-coordinate chain for nested regions, fragment ordinal,
repetition provenance, and visibility. An existing source omitted from layout
has no locations. An absent optional reference also has no locations; authored
queries distinguish absence from omission. Required lookup uses query `.one()`
or `ctx.region`, while `.optional()` and `find_region` express intentional absence.

A fragmented item can occupy pages 2 and 3; the caller decides how to format that
set. Repeated table headings must be marked as repetitions, not mistaken for
additional content. Location queries must not invent pixel coordinates for
Discord. Future geometric facts need an explicit target capability.

## Resolution and search integration

The current pipeline expands components, explores offers and partitions, builds a
`PageFamily`, certifies every reachable coordinate combination, and retains the
result as an `AllocationSnapshot`. Its `project` operation cannot change layout
costs. Therefore inserting generated text after certification is unsound.

The proposed integration is inside candidate evaluation, before certification:

1. Start with contextual bootstrap content and construct a provisional family.
2. Build immutable fact indexes for that family and its coordinate views.
3. Evaluate contextual nodes whose observed facts changed. Record their reads.
4. If output changed, rebuild affected semantic/offer/partition work; new content
   can change available representations and introduce regions. Old cuts and
   resource certificates are invalid until checked again.
5. Certify every reachable scene only when content and observed facts stabilize.
   Publish facts with the successful candidate, then commit after delivery.

This is not repeated application `render()`. Each candidate has isolated working
content and facts. A cursor turn may select already certified contextual output
when the complete family remains valid. All callbacks, query indexing, iteration,
and coordinate enumeration consume the existing shared planning work allowance.
Dependency reads initially track complete query results; finer property tracking
is an optimization, not required for correctness.

Required facts can be provisionally unavailable. Such a read suspends that block
and retains its bootstrap/previous working content while other blocks progress.
If the graph becomes quiescent with required reads unresolved, fail with the
context key and missing target. Optional absence is a valid query result. Merely
using zero or the last delivered answer for an unresolved fact would mask errors.

Fixed-point equality includes generated content and relevant layout choices,
not just the displayed text or page count. Detect repeated working states and
bound nonrepeating growth. Diagnostics should include the contextual key, queried
reference, observed values, and iterations. Caches require render-snapshot,
candidate, occurrence, callback, and coordinate identity; query dependencies may
permit sharing within that boundary. No answers are written to `sl.presentation`.

## Component preparation and visibility

Contextual content may place component slots declared before planning. The runtime
can discover their keyed component instances without executing every layout
branch. Callbacks choose among prepared slots; they cannot introduce undeclared
instances or construct components during layout. The slot declaration and its
generic target/context signature still need a typing pass.

Use eager preparation by default: discover declared slots, prepare independent
components concurrently, capture their rendered subtrees, then begin layout.
Planning has no asynchronous loading pauses. Load a shared component instance
once even when it has several declared placements. Keep loading policy separate
from query and layout APIs so explicit lazy slots can be considered later.

`on_load()` holds work needed before the component can render its first useful
state. Required database or network requests are legitimate; expensive optional
enrichment should use a useful initial/loading state and the resource system.
Eager preparation may wait for a slot that the final layout omits. It does not
start that component's owned services during speculative preparation.

Mounting follows the committed logical slot declaration. A hidden slot retains
its state, presentation cells, history, and mounted services until its declaration
or owner is removed. Proposed `on_show` and `on_hide` hooks report per-placement
visibility changes after successful delivery. Provisional candidates trigger
neither hook. A placement is shown when any fragment appears and hidden when its
last fragment leaves the active delivered interface; this is not viewport tracking.
Shown placements receive `on_hide` before their eventual unmount. Start with
synchronous notifications alongside the existing mount/unmount hooks; ongoing
work uses owned services. Visibility-dependent activity can pause or resume
without changing logical ownership.

## Preparation diagnostics

Tooling is part of the preparation contract. Record per-component preparation
wall time, outcome, whether loading was reused, and the declaring slot paths.
Report batch elapsed time separately: concurrent durations are not additive.
If concurrency is limited, distinguish queueing time from load execution.

Developer inspection and slow-preparation diagnostics should identify which
required load delayed delivery, separately from layout time, resolution rounds,
and planning work. Slow thresholds are configurable diagnostics, not implicit
timeouts or layout-ranking inputs. Keep these details in developer tooling.

## Candidate failure

Reject a nonconverging candidate and consider other declared alternatives. Retain
the rejected-branch diagnostics. If no candidate can be certified, fail without
replacing the last delivered UI. Work exhaustion must not be reported as proof of
infeasibility. Hidden regions retain their explicit optional selection, and
location queries retain all fragments rather than choosing an arbitrary page.

## Experiment results

Eleven tests in `spikes/test_experiment.py` pass. Both Pyrefly and BasedPyright
accept the signatures/examples and reject the six negative probes for the intended
type errors. The experiment remains separate from the production planner.

| Experiment | Result |
| --- | --- |
| Real HTML allocation: 83 keyed builds, 12 per page | Existing metadata reports 7 pages, selected index 1, 12 visible sources, and build 42 on page index 3 |
| Synthetic footer changes its own available page space | Page counts 6, 13, then 15; stabilizes in 4 passes with 3 callback evaluations |
| Turn the selected page with unchanged page count | Only the callback reading the selection reevaluates; a constant callback is reused |
| A required fact appears after another block generates content | The dependent block suspends provisionally and resolves on a later pass |
| A required fact remains absent | Quiescence fails with the context key and missing query |
| Alternating output and unbounded growth | Repeated states fail as oscillation; nonrepeating growth reaches the iteration bound |
| Candidate/snapshot changes and work exhaustion | Caches remain isolated and layout plus query operations share one allowance |

The real allocator test reads `RegionPages`/`AllocationSelection` metadata and
checks that the previously selected output remains the same object. It does not
construct another page family. The source anchors can answer direct page
membership but cannot distinguish exact split fragments from repeated chrome.
Build the richer location index from `FragmentCoverage` and repeated-source
metadata before certification discards that provenance. Nested addresses also
need parent/child page-coordinate joins, which the simple membership adapter
deliberately does not implement.

`spikes/experiment.py` contains an isolated string/row model. Its deterministic
layout is a function of generated text, so comparing text states is sufficient
there; production equality must include graph, representation, and partition
choices. It demonstrates mechanics, not production search integration, resource
calibration, global optimality, lifecycle behavior, or target certification.
Its eight-round default and synthetic work units are experimental parameters,
not proposed production limits. Previously returned certificates remain immutable
when a new attempt fails; actual delivery preservation remains the host's job.

Run the executable experiment from the repository root:

```sh
PYTHONPATH=docs/plans/layout-introspection/spikes uv run --locked pytest docs/plans/layout-introspection/spikes/test_experiment.py -q
```

For explicit Pyrefly checking, add the spike directory with `--search-path` and
pass its `.py`/`.pyi` files. BasedPyright needs an explicit spike include (docs are
normally excluded), a merged workspace package tree as in CI's installed-package
approach, and its missing-source warning disabled for the intentional stub module.

The next implementation slice must connect contextual content to candidate
construction and recertification, using a page footer as the first query consumer.
It should preserve the general query boundary and produce a measured integration
cost before adding a broader public vocabulary.
