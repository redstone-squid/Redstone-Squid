# Layout introspection: design and typing spike

This is an experiment, not a production API or a change to pagination. The agreed
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

`contextual(callback, key=..., initial=())` returns a layout node. `initial` is
bootstrap content, not a user-visible fallback. Its output preserves the callback's
render-target and interaction-context types. The spelling avoids colliding with
the existing `squid_ui.context` module. Names remain provisional.

```python
results = region_ref("results")


def footer(ctx: LayoutContext) -> sl.LayoutNode:
    pages = ctx.region(results)
    selected = pages.selection
    if selected is None:
        return sl.paragraph(f"{pages.count} pages; results are hidden")
    return sl.paragraph(f"Page {selected.number} of {pages.count}")


# Inside the ordinary component render:
contextual(footer, key="results-footer")
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

Reuse `Selector[T]`, `Query[T]`, and `Match[T]`; do not create another selector
language. Context exposes three explicit scopes:

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

## Decisions to review before production

These refine the agreed direction; the spike does not silently implement them:

- Hidden regions expose `selection=None`, and location queries return all
  fragments. Recommend this explicit treatment over selecting an arbitrary page.
- Recommend querying contextual output through `placed`/`visible`, while `authored`
  remains the fixed application snapshot. This supports generated-document queries
  and gives summaries a stable authored denominator.
- Recommend allowing generated document nodes and controls first, while rejecting
  `Component`/`Boundary` output until layout-dependent component mounting has a
  lifecycle design. This is a real expressiveness boundary, not a typing detail.
- Recommend rejecting a nonconverging candidate and considering other declared
  alternatives. Fail the document if none can be certified; retain rejected-branch
  diagnostics. Work exhaustion must not be reported as proof of infeasibility.

## Experiment

The executable spike will separate two questions: which facts the retained
allocation already provides, and whether dependency-tracked iteration can handle
page-count feedback, delayed facts, oscillation, and growth under a shared budget.
A small row/character model can test resolution mechanics, but cannot prove that
production offer/partition search is integrated or optimal. Record that boundary
with the results before starting a production footer implementation.
