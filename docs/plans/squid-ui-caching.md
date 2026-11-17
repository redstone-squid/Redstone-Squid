# Dependency-based caching for squid-ui

Status: incremental implementation started. Callback-independent allocation reuse
for ordinary semantic `ActionControl` handlers is implemented; the broader artifact
and dependency model below remains proposed.

## Implemented increments

A committed owner can reuse a certified allocation when only `on_trigger` changes.
`ReboundAllocation` selects the existing page Scene and refreshes bindings through
explicit framework handler edges, preserving frontend selection and grouped routes.
It retains allocation identity, does not mutate the committed generation, and flattens
successive wrappers so callback updates cannot form an unbounded chain.

All other control fields still participate in allocation comparison. Forms, native
controls, and actions inside alternative/fallback presentations remain conservative.
This is owner-local reuse: the underlying certificate still contains its original
callback-bearing outputs. It is not yet the callback-free shared artifact model below.
Rebound page projections now have an owner-local LRU capped at 32 entries. It is
keyed by the certified output identity, so clamped or hidden coordinates that select
the same output reuse one projection. Controlled page turns keep the cache; changed
callbacks get a fresh generation. Failed frontend binding stores no entry. Cache
snapshots expose entry, hit, miss, and eviction counts without exposing callbacks.
The cap bounds entry count, not the byte size of arbitrary captured application data.

Prepared semantic graphs and Document fingerprints survive replacement of a retained
result. Region content fingerprints are computed once per retained authored snapshot,
then reused across page turns. These caches never retain cursor decisions or delivery
outcomes: presentation still resolves current positions and stages updates each time.

General incremental subtree fingerprints, callback-free shared artifacts, byte-based
cache accounting, and partial certification remain future work.

Tests cover callbacks on every page, isolation from the previous generation, repeated
updates, frontend selection, shared callback identity, and invalidation by display or
dispatch-policy changes. Projection tests cover LRU eviction, failed binding, coordinate
clamping, generation isolation, and retention across controlled page turns. Work-count
tests verify that page revisits do not rebuild the graph or rehash unchanged content.
Existing composition and Discord cache tests exercise the surrounding planning and
rendering paths.

A local microbenchmark of 100 callback-only updates to a paragraph and one action
measured median planning time of about 0.51 ms with a fresh presentation state versus
0.21 ms with the retained allocation. Target evaluations fell from 100 to zero.
This is illustrative planning-only evidence, not an end-to-end latency guarantee.

## Objective

Avoid recomputing results whose inputs have not changed, while preserving current
handlers, valid layouts, lifecycle behavior, and accurate knowledge of what Discord
actually displays. Keep planning and drawing separate. Let the runtime coordinate
reuse through explicit dependency records rather than inspecting their internals.

A cache hit must answer two questions: which result can we reuse, and what proves
that it is still valid? A component key identifies a piece of content; it does not
prove that its text, behavior, resource cost, or environment is unchanged.

## First principles

1. Reuse applies to a particular output and its dependencies, not to a whole screen
   merely because it looks similar. Different outputs have different dependencies.
2. Pure artifacts and live behavior have different lifetimes. Shared caches may
   retain immutable descriptions, never callbacks, mutable SDK objects, file streams,
   message handles, or authority to act on behalf of a user.
3. Layout validity is global unless independence has been proved. Hidden content can
   change page boundaries, navigation, and the space available to visible regions.
4. Computing a candidate is not presenting it. Reusing a computation cannot establish
   that delivery succeeded or advance committed presentation state.
5. Unknown dependencies cause recomputation. Extensions and mutable inputs must not
   gain reuse merely because their Python identity or filename stayed unchanged.
6. Caching must save more work than validating it costs. Hashing the entire Document
   on every interaction is still a full traversal, even when the cache hits.

## What exists today

The component runtime's `RenderCache` retains render observations and expanded
subtrees, including dependency invalidation and lifecycle reconciliation.
`PlanMemo` retains an owner's callback-bearing result for exact input identity and
accepted presentation revisions. `PlanCache` stores callback-free partition hints;
those hints require current certification. `RetainedComposition` reuses committed
allocations for compatible page changes. `RenderProgramCache` stores constructor
instructions and audit status, while each draw creates fresh SDK objects.

Message publication can compare the candidate with the live Scene before drawing,
suppress an unchanged presentation, and update bindings behind existing control IDs.
These mechanisms are useful foundations. This proposal does not assume they are
missing or that their cost dominates without measurement.

## Separate the reusable results

Introduce internal immutable artifacts; these names describe proposed contracts,
not new public APIs. Keep `plan()` and `PlanResult` as the public interface.

| Artifact | Contents | Required dependencies | Lifetime |
|---|---|---|---|
| Prepared fragment | Resolved content, semantic structure, representation offers, dependency record | Authored values, context reads, localization, target rules involved in preparation | Owner by default |
| Certified allocation | Representation choices, page boundaries, measurements, validity certificate | All layout-relevant fragment inputs, limits, reservations, navigation, policies | Owner initially |
| Page projection | Scene for a selected page combination and binding/resource slots | Allocation identity, selected positions, display inputs, resource descriptors | Bounded owner cache |
| Binding snapshot | Current action/form handlers and resource providers for the slots | Current component render and binding preparation inputs | One owner, candidate or committed |
| Drawing program | Immutable instructions for building SDK objects | Scene, renderer implementation/version, extension compilation inputs | Bounded renderer cache |
| Presentation record | Actual delivered Scene, attachment identities, control topology and generation | Successful delivery or verified unchanged presentation | One live message |

Do not store a whole `PlanResult` in a shared cache. Compose it from reusable pure
artifacts and current owner-local bindings. Preserve exact owner-local memoization
when every dependency, including bindings, remains valid.

## Dependency identity and validation

Use immutable node versions and retained observation records to avoid walking
unchanged subtrees. A reused subtree can carry its previously calculated fingerprints;
a changed leaf requires recomputing only its ancestors. Explicit refreshes that sample
nonreactive inputs still execute the relevant component render before claiming reuse.

Keep distinct dependency projections:

- **Preparation:** semantic values, stable scoped keys, context and localization reads.
- **Layout:** representation offers, exact resource costs, break constraints, fidelity
  contracts, control structure, attachments, and target capabilities/limits.
- **Display:** selected content, styles, enabled state, labels, route payloads, and
  descriptors of resources used by that page.
- **Bindings:** action/form implementation and behavior, including guards and schemas.
- **Delivery:** displayed content plus attachment revisions, routing/control topology,
  mention settings and other transport-affecting options.

These are dependency sets, not five unconditional whole-tree hashes. An input can
belong to several sets. A button label affects both layout and display; a new closure
can affect only bindings; a changed form schema can affect bindings and layout.
Start conservatively: classify uncertain inputs as affecting preparation and layout.

The planner owns the extraction of layout dependencies. The runtime must not guess
that equal text length implies equivalent layout. Break opportunities, escaping,
representation selection, control adaptation, and fidelity can still differ.

An environment key includes target identity and implementation revision, capabilities,
limits, reservations, strictness, composition policy, locale/catalog revision, palette,
chrome/navigation, and extension versions as applicable. Begin with a coarse key;
split it only where measurements justify narrower dependencies. Search budget and
search policy remain part of request compatibility: a cached layout must not silently
change the meaning of an explicitly bounded or fixed-choice request.

Within an owner, immutable artifact identity is an inexpensive equality proof.
Cross-owner sharing requires canonical values and explicit shareability. Hashes are
lookup aids; retain comparable canonical keys where collision could affect correctness.
Opaque or externally mutable values are uncacheable unless an adapter supplies a
reliable revision and declares what it covers. A URL is not a content revision.

## Allocation reuse requires evidence

A certified allocation records the exact layout dependencies and environment under
which it was checked. Exact matches can reuse it without search or recertification.
A previous partition without that match remains only a hint.

Initially, any changed layout dependency requires certification against current
content. Reuse prepared fragments and try previous choices/cuts first, but fall back
to the normal planner when they fail. Do not claim that failure of old cuts proves
that no valid layout exists.

Later, a target may provide an equivalence contract that permits replacing content
inside a certified slot. The contract must cover every affected constraint and choice,
not merely a scalar cost. Until that contract exists, even equal-cost text changes
require certification. This deliberately limits the first implementation's ambition.

Hidden-page changes must update the allocation unless their layout dependencies are
proved unchanged. Only after validating the allocation may the runtime conclude that
the selected Scene stayed the same. For independent regions, finer certification is
possible only if the certificate records their shared budgets and cross-region
constraints; independently valid regions are not automatically valid together.

## Refresh algorithm

1. Capture the owner revision and environment. Expand dirty components using existing
   reactive observations, retaining unchanged fragments and their dependency records.
2. Prepare changed fragments. Obtain current binding snapshots even when their visual
   output equals the old output. Never compare closures by function name or bytecode.
3. Validate the allocation certificate. Reuse an exact match; otherwise certify current
   inputs using previous cuts as a hint or perform normal search.
4. Select pages and build/reuse their Scene projections. Resolve current bindings and
   resource slots. A page-selection-only change should stop short of allocation search.
5. Compare each message with its committed presentation record. If all transport
   inputs match and the live generation is usable, skip drawing and delivery, but
   commit current bindings and reconcile lifecycle changes.
6. For changed messages, reuse/compile drawing instructions and create fresh SDK
   objects with current wiring. Audit mounted controls and opaque extensions as today.
7. Deliver through current authority. Commit presentation updates for the accepted
   result and record actual writes. Failure retains dirty work and the prior displayed
   record; pure artifacts may survive only if valid independently of delivery.

Before publication, check whether the captured revision was superseded. Follow the
existing serialization policy: discard/recompute a stale candidate, or finish its
ordered delivery and retain a pending refresh. Never mark intervening writes clean.
A cached artifact grants no access permission and cannot renew an expired edit handle.

For multi-message output, preserve per-message write tracking and existing partial
failure semantics. A successful write to one continuation does not prove that another
message displays its candidate. Do not introduce an all-or-nothing delivery fiction.

## Concrete expected behavior

| Change | Expected work |
|---|---|
| No observed input changed | Reuse component result and exact owner plan; no draw/edit |
| Callback captures a newer model, with unchanged display | Refresh binding snapshot; reuse certified allocation and Scene; no draw/edit |
| Next page selected | Reuse allocation; select projection; draw/edit changed messages |
| Visible label changed | Prepare changed fragment; validate layout; update Scene and affected message |
| Hidden item added | Revalidate allocation and navigation; edit only if resulting visible output changes |
| Locale or limits changed | Invalidate affected preparation/allocation; replan |
| Resource contents changed behind a stable filename | Require resource revision; update delivery even if text is unchanged |
| Native extension has no revision contract | Recompute its affected stages and audit output |
| Delivery fails, then identical refresh retries | Reuse valid pure work; retry delivery rather than suppress against an undelivered candidate |

## Ownership and memory

Start with owner-local preparation, allocation, and projection caches. Bound projections
by retained bytes and entry count; do not materialize every page combination in a cache.
Keep certificate representation compact and preserve the planner's existing search
budget. Cross-owner fragment sharing is a later optimization, allowed only for explicit
immutable, callback-free values with no owner context or user-specific retention risk.

Closing an owner clears bindings, observations, and owner artifacts. Eviction never
unmounts components, cancels application work, or changes behavior. Drawing programs
remain separately bounded; share them only when extensions satisfy the same purity
contract. No disk cache or distributed cache is proposed.

## Validation and rollout

First add diagnostic counters for component renders, preparation work, certificate
checks, search evaluations, Scene construction, instruction compilation, SDK object
construction, edits, and retained bytes. Record why a stage missed reuse. Benchmark
page turns, new closures with identical output, single-item edits, hidden-page edits,
locale changes, and failed-delivery retries. Include cold starts and small documents:
cache bookkeeping must not quietly make the ordinary case slower.

Implement in independently reviewable steps:

1. Add measurements and a cache-disabled reference path for tests.
2. Separate callback-free allocation/projection data from current binding snapshots,
   retaining existing public results and exact memo behavior.
3. Add dependency records to prepared fragments and propagate unchanged identities.
4. Reuse exact allocation certificates and page projections through the existing
   publication preflight. Preserve hint-based fallback on any uncertain dependency.
5. Consider finer equivalence contracts or cross-owner reuse only after profiling.

Differential tests compare cached execution with a cold execution under the same
presentation history and deterministic planning policy. Compare Scenes, diagnostics,
resource selection, session updates, and observable action behavior; metrics may differ.
If allocation stability permits multiple layouts, test the same feasibility/fidelity
and anchor contracts rather than requiring unrelated cold searches to choose identical
page cuts.

Test generated edit sequences: reorder/remove keyed content, replace handlers, switch
locales, change limits, navigate, fail delivery, mutate during delivery, and close the
owner. Explicitly exercise equal-looking buttons with different behavior, attachment
revision changes, stale interactions, partial continuation delivery, extension fallback,
and lifecycle invalidation during commit. Assert stage counters for the expected fast
paths so correctness tests cannot pass while every operation silently recomputes.

Accept each phase only if these invariants hold and measurements show reduced work
without material cold-path or memory regression. The architectural gain is reusable
proofs passed between stages; fewer independent caches is not itself a goal.
