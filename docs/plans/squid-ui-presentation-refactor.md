# Allocation presentation ownership

The audit followed authored semantic and native nodes through the public planner,
input preparation, semantic graph construction, target offers and evaluation,
allocation search, cursor reconciliation, staged updates, and frontend binding
preparation. HTML and Slack realization paths were compared with Discord's native
input policies. Runtime staging was checked separately: prospective component
subscriptions and lifecycle changes remain distinct from delivered presentation
state.

## Ranked findings

1. **Presentation ownership.** Certified output, authored structure, cursor
   reconciliation, and retained reuse described one operation but had separate
   owners. `present_composition` rebuilt the graph and fingerprint on each turn,
   constructed retention records, and created a recursive page-selector closure.
   A pager change required following loose arguments across planning, retention,
   and selection. This was the chosen scope.
2. **Planning modes.** Implicit planning runs input preparation and derives an
   automatic work allowance; explicit composition options take a different path.
   Some differences express deliberate API choices, including default navigation
   and fixed-choice rejection. Consolidating them requires deciding those semantics
   first; moving both branches into one helper would not resolve the problem.
3. **Native vocabulary in the shared core.** Input preparation knows Discord
   primitives and their nesting restrictions. Target-supplied `InputPolicy`
   separates sizing facts, but adding a native vocabulary still edits core
   traversal. A target preparation contract could improve dependency direction,
   provided it does not duplicate semantic traversal in each backend.
4. **Type erasure at dialect selection.** `TargetDialect.rules` uses `Any` for
   payload, resource, and preparation types, while the allocation engine preserves
   those relationships internally. A larger dialect redesign should preserve this
   association rather than add casts to backend implementations.

The package already separates authoring, planning, interactions, runtime, scenes,
and target extensions. Organization was not the primary obstacle, and no file
split was selected as the principal change. HTML and Slack share representation
selection while retaining their own output evaluation; their different platform
limits and rendering formats are necessary complexity.

## Completed design

Before, initial search and retained reuse both called
`present_composition(result, rendered, request, options)`. That operation rebuilt
semantic structure, assembled updates, and captured those same arguments in a
nested callback. The retained record could compare inputs but could not present
its own allocation.

Now initial search creates `RetainedComposition.prepare(document, request,
options, result).present(options=options)`. A matching retained allocation uses
`retained.reuse(document, request, options)`. The owner prepares the graph and
fingerprint once, resolves current cursors in `present`, and exposes that method
as the page selector through a bound partial. The free presentation function and
recursive callback disappear. Construction owns navigation-version capture and
normalization of transient page coordinates.

Controlled page values are deliberately excluded from allocation equivalence.
They still affect what the reader sees: `reuse` refreshes the presentation owner
when authored values differ, preserving certified cuts while rebuilding the graph
with current ownership. An unchanged authored snapshot uses the existing graph.

This owner retains the authored document, certified callback-bearing result,
configuration, and live presentation session through its request. That retention
already existed in the old record and callback. It is intentional: page selectors
must read pending cursor requests from the current session. The graph and
fingerprint describe the authored snapshot; they do not snapshot mutable cursor
state. Delivery stages the owner as a session update, so failed delivery does not
replace the committed owner.

## Assessment and deferred API proposal

The complete initial-plan, delivery-update, retained-turn, and direct-selection
flow now has one presentation owner. Existing tests cover staged writes, hidden
pagers, controlled coordinates, native wrappers, explicit page coordinates, and
allocation reuse. A retained-turn regression check additionally rejects semantic
graph rebuilding for unchanged content. The focused composition suite passes.

The remaining findings concern different boundaries and do not require parallel
presentation pipelines. They remain explicit follow-up work, rather than claims
that this pass redesigned every planning mode or target contract.

`PresentationState.retained_composition` and `RetainedCompositionUpdate` currently
expose cache implementation details. A concrete future breaking change would make
that retention private and have successful delivery apply an opaque planning
commit operation. Ordinary callers would still request pages and apply delivered
updates, without constructing cache records. The public field is preserved in
this pass pending a compatibility decision; changing it is not necessary to give
presentation behavior and its prepared state the same owner.
