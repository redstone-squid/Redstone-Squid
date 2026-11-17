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

## Completed follow-up boundaries

Built-in execution now belongs to `CompositionPlan`. It resolves implicit versus
explicit policy, prepares input when required, controls exact memo eligibility,
and owns allocation reuse and target execution. The public dispatcher selects
built-in or custom planning and performs the common final binding step.

The mode differences are deliberate: implicit planning normalizes portable and
native authoring input and derives a search allowance; explicit options preserve
authored occurrence IDs and the requested allowance. Exact memo replay remains
implicit-only because its key does not include explicit composition options.
Custom planners retain control of their vocabulary and planning strategy.

Native input preparation now belongs to the target. `InputPolicy.native` creates
a visitor for each traversal. Discord owns embed, component, extension, and
overflow checks; that visitor delegates portable children back to the shared
`InputPreparation`. Adding a native vocabulary no longer requires adding cases
to the shared input walker. The portable conditional-variant form still uses its
existing shared normalization. Unsupported nodes on targets without a native
visitor are reported by node type.

The dialect contract returns a `TargetComposition` through `composer`, rather
than returning rules with erased payload, resource, and preparation types.
`RuleComposition` binds those types while they are still known and carries them
through search and output construction. Concrete dialects retain their typed
`rules` factories for direct use.

For custom dialects, replace the `InputPolicy.native_types` list with a native
visitor factory, and implement `composer(document, limits)` by returning
`RuleComposition` around the concrete rules. Built-in targets and their callers
have been migrated.

The retained composition still holds the live presentation session intentionally:
page selection reads current cursors, while the graph and fingerprint describe
authored content. Delivery remains the point at which staged state is committed.
