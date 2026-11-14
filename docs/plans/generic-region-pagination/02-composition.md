# Shared composition and target participation

Status: complete. The shared composition boundary
is available through `plan(..., composition=...)`. The [API sketch](api-sketch.md) records the
implemented author syntax and target contract. Automatic joint search remains Phase 3 work.

Establish the first usable boundary between the rich semantic planning core and target-specific
realizations. The [Phase 1 handoff](01-shared-core.md#phase-2-handoff) supplies the requirements,
and the [composition experiments](composition-experiments.md) supply initial acceptance cases.
The joint allocation algorithm and ranking policy remain Phase 3 work.

## Agreed design choices

### Redesign author APIs and migrate callers together

Existing signatures for regions, pagination, and alternatives do not constrain the new model.
Choose coherent APIs and migrate callers with the corresponding changes. Compatibility adapters
are not a requirement. Migration still needs to preserve the agreed content, interaction, and
delivery guarantees and identify intentional behavioral changes.

### Use one alternatives API

Alternatives carry explicit fidelity and preference metadata. Fidelity describes preservation
of content, relationships, and behavior; preference ranks permitted choices. Strictness and hard
requirements determine admissibility independently of presentation preference.

All candidates use the same API shape; the first presentation is not a special semantic reference.
They refer to shared semantic content independently of selection. An authored declaration of
equivalence is not proof that arbitrary prose has the same meaning; the core distinguishes what
it can validate structurally from what the author declares.

### Permit atomic extensions with declared planning behavior

Custom or target-native widgets may remain atomic when they expose their constraints, resources,
and effects. The core can place and account for such widgets without inspecting target payloads.
Adaptation requires richer semantics: the core must not invent internal breaks or transformations
from an opaque payload. Target adapters cannot perform undisclosed fitting after selection.

### Offer built-in lossless alternatives by default

Semantic widgets and targets supply lossless alternatives without requiring authors to enumerate
every representation. Authors can constrain the permitted representations and express preferences
among them. Automatic adaptation still respects hard requirements, content relationships, and
interaction behavior; a different visual shape alone is not evidence that a transformation is safe.

### Declare specific losses with concise helpers

Author-supplied lossy alternatives describe the particular content or behavior changes they allow.
Examples include omitted fields, shortened text, and changed interaction behavior. Concise helpers
should make common declarations practical while exposing structured effects to shared planning.
Coarse labels such as summarized or partial are not sufficient substitutes for those declarations.

Omission, source-referenced shortening, and named interaction changes form the initial effect vocabulary.
Author preference ranks permitted alternatives; it does not replace describing what an alternative
changes or authorize undeclared loss.

### Establish meaning through shared semantic content

Built-in alternatives derive from their authored semantic node. Custom alternative groups identify
shared semantic content separately from their presentations, using the existing vocabulary of
text, fields, tables, controls, and relationships. Presentation helpers carry references to that
content, while explicit correspondences cover combination, splitting, or other reorganizations.

Preserve semantic identities, relationships, and interaction scope through those correspondences.
The core can validate structural preservation and detect known omissions. Author declarations
remain necessary for arbitrary paraphrasing; they must not be confused with machine-verified
semantic equivalence. No candidate becomes the reference merely by being first or preferred.

## Implementation sequence

Each step should land as reviewable, validated changes. Refine concrete names and types while
exercising the agreed contracts. Pause for discussion if a finding changes a policy, introduces a
new author obligation, or requires changing the established content or delivery guarantees.

### 1 Introduce semantic references and alternative declarations

Build on the authored semantic nodes to describe source units, relationships, and occurrences.
Introduce the symmetric alternatives API with explicit shared content, choice identity, fidelity,
and preference. Keep semantic identity independent of branch order and selected presentation.

Provide correspondence for direct reuse and for a small combined/split presentation example.
Start the effect vocabulary with concrete omission and shortening, and distinguish declared
behavior changes from preservation. Helpers should carry the references needed for checks rather
than forcing callers to repeat a separate identity tree.

Validate reordered candidates, unresolved references, duplicate identities, known omissions under
an equivalence claim, and strict acceptance of declared equivalent alternatives. Record the limits
of structural validation for author-written text.

The semantic walker uses explicit typed node handling. Unsupported vocabulary raises an
error until its semantics are implemented; a node with `children` or `key` attributes is not
implicitly supported. Current checks cover unit correspondence, known omissions, and action,
route, link, picker, toggle, and form behavior. Source constraints remain in the graph.
Source-referenced text fragments now resolve shortening after localization, preserving source
ranges and inline styling. Arbitrary prose remains an author assertion; unsupported rich inline
shortening returns a candidate failure. Contextual composition now enforces strict admission and structural budget boundaries; correspondence
checks alone do not certify a valid plan.

### 2 Establish composition and target contracts

Implement the target offer, evaluation, construction, and validation responsibilities from the
sketch. Candidate compositions retain semantic correspondence, selected effects, shared-resource
identities, container context, and interaction references. Target payloads may remain opaque when
all decision-relevant behavior is exposed; native extensions remain atomic without richer semantics.

Use structured outcomes for infeasible candidates, unsupported capabilities, invalid declarations,
and work exhaustion. Preserve the distinction between one infeasible candidate and proof that no
layout exists. Pass one work-budget context through target operations; targets cannot reset it or
run an undisclosed fitting search.

Validate shared-resource deduplication and conflicts, repeated container/navigation costs, local
nesting constraints, and construction that disagrees with evaluation. The final builder must not
silently change content to repair an invalid result.

### 3 Exercise V2 and Slack directly

Implement realizations for the representative semantic subset needed by paragraphs, tables,
authored alternatives, shared resources, and persistent containers/navigation. V2 and Slack consume
the shared semantic model directly. Reuse appropriate formatting and construction helpers, without
calling another complete target planner to discover representations or fit results.

Use explicit candidate configurations to test composition and evaluation before the full joint
search exists. In particular, verify the table probe preserves source row/column identity and
reports infeasibility or declared effects instead of silently slicing content. Verify the shared
attachment case accounts for one identified asset across region boundaries.

### 4 Check unbounded planning and interaction ownership

Exercise HTML through the same contracts without introducing global message limits. Check that
explicit authored constraints still apply and that all targets retain the same semantic references
through different realizations.

Carry visible action/form references and live bindings through selected compositions. Planning
must not invoke change handlers or commit presentation state. Verify controlled and routed pages
remain application-owned, discarded candidates do not publish state, and native atomic widgets
expose their required resources and constraints. Full state-path migration remains phase 4 work.

### 5 Integrate the boundary and migrate affected callers

Connect the validated contracts to the existing planning entry points at a clearly documented
migration boundary. Migrate affected APIs and callers together; compatibility adapters are not
required. Preserve unmigrated functionality with an explicit inventory and removal phase for any
temporary routing. Do not hide incomplete joint search behind a claim of infeasibility, or rebuild
an independent allocation engine inside a target adapter.

Convert the investigation probes into regression and conformance cases. Update author documentation
and the API sketch to the implemented names and behavior. Hand the shared candidate/evaluation
model and its tested invariants to phase 3 for the full joint solver.

## Implemented boundary and migration inventory

`plan(..., composition=CompositionOptions(selections))` evaluates explicitly selected representations
through shared composition. A sole offer needs no selection. Multiple offers require a choice by
`OccurrenceId`, and unknown selection paths are errors. `CompositionRejectedError` describes one
failed selection; it does not claim that another layout cannot fit. Work exhaustion remains a separate
error. The opt-in path bypasses the legacy target planners and their caches.

The composition core owns source correspondence, strict admission, enclosing resource budgets,
atomic requirements, live binding collection, and the shared work allowance. Targets prepare immutable
output once and independently validate construction. The core does not inspect concrete scene bodies.
Target-specific preparation remains typed throughout; the public dispatcher checks the body type at
the boundary to the existing `PlanResult` API.

Checked alternatives and source-referenced text now compose on V2, Slack messages, and HTML.
Nested groups retain shared source identity. Source fragments resolve after localization, retain
provenance, and reject unsupported rich shortening. Arbitrary rewritten prose still depends on the
author's assertion. Effect reports retain their source scope.

Resource budgets use `squid_ui.presentations.budget(node, limits={Axis.COMPONENTS: 20})`. They are
structural boundaries. Alternatives may rearrange content inside them; alternatives replacing a
larger region must preserve inner boundaries with equal or tighter limits. Shared assets count once
within each budget. Platform limits and reservations always apply. Unsupported axes reject the
candidate, and budgets never authorize loss. Visual-length and allocation preferences remain separate.

Selected controls retain source references and live handlers. V2, HTML, and Slack exercise actions,
links, and durable routes; V2 also exercises modal form triggers. Planning invokes no action, submit,
page-change, or route callbacks and publishes no presentation state. Regions are complete single-page
compositions in this phase. Their ownership remains application-owned; overflowing regions reject
rather than silently omitting content or pretending to have performed pagination.

Custom widgets subclass `AtomicNode`, retaining a typed payload. Their rules supply `AtomicContract`
with additive and shared-resource costs, required capabilities, and legal placements, together with
author-authorized effects. The core checks those declarations against evaluation. It does not infer
children or shortening operations from an opaque atom. `FixedComposition(rules, selections)` connects
custom typed rules to the same public dispatcher and validation pipeline.

The build-log consent banner is the first application caller using the opt-in path. It now uses
semantic text and a routed action, retaining the existing notice and route. The clipboard icon is
part of the button label. A frontend regression test disables the legacy planner while drawing it.

| Unmigrated area | Current behavior | Removal or replacement phase |
| --- | --- | --- |
| Automatic representation selection and pagination | Explicit choices only; no independent target search was added | Phase 3 joint solver |
| Mounted runtime traversal, page reconciliation, navigation and state updates | Existing runtime paths; opt-in planning does not consume or commit their state | Phase 4 |
| Structural caches and live memo reuse | Bypassed for opt-in composition | Phase 5 |
| Classic Discord, Slack Home/modal, remaining semantic widgets and native extension registries | Existing planners; opt-in fails explicitly for unsupported vocabulary or targets | Phase 6 |
| Production `sl.budget(min=..., prefer=..., stretch=...)`, `paged`, truncation and fallback helpers | Legacy behavior retained outside opt-in; workbench description and paged callers need intent-based migration | Phases 3 and 6 |
| Document-level assets and external pagination options | Rejected by the fixed-composition entry point; downloads within supported content work | Phases 4 and 6 |

These routes are temporary migration boundaries, not separate long-term solvers. V2 panels,
paragraphs, tables, downloads, persistent region content, and selected controls supply the current
representative vocabulary. Slack participates directly, without HTML compilation or silent table
slicing. HTML has no invented global resource axes. Further widget coverage belongs to Phase 6.

## Phase 3 handoff

Use `SemanticOccurrence`, `RealizationOffer`, `CandidateComposition`, and `Evaluation` as the search
boundary. Keep source identity separate from presentation choice and placement. Replace explicit
selection with joint search without changing the build-from-preparation contract. Preserve the
separation between candidate rejection, proven infeasibility, and exhausted work.

Choose ranking and sound pruning rules explicitly. Equal numeric costs do not make candidates with
different shared resource identities interchangeable. Preserve budget, unbreakable, keep-with-next,
and region boundaries when introducing legal page breaks. Include repeated headers, footers, and
navigation in every relevant evaluation. Runtime page-path and delivery migration remains Phase 4.

## Validation and acceptance

The working implementation must demonstrate:

- Peer alternatives tied to a shared semantic source, with fidelity separate from preference.
- Built-in lossless alternatives and explicit, specific permissions for content loss.
- Direct V2 and Slack participation, plus an unbounded HTML case.
- Correct resource sharing and context-dependent composition, not only additive leaf costs.
- Semantic identities and relationships retained through table and compact presentations.
- Atomic extension handling without undisclosed transformations.
- Consistent candidate failures and work accounting, with no state publication during planning.
- Target output that honors the selected composition, verified by independent target checks.

Validation completed: 1,055 tests passed across the full portable UI suite, Discord rendering,
form planning and regions, and the migrated consent banner. Whole-project Pyrefly reported zero
errors; changed-file lint, formatting, and `git diff --check` passed.

Run focused tests with `--no-cov` during development and after final changes, including the affected
semantic, target-conformance, region, and frontend integration suites. Run whole-project type checking
and relevant lint/format hooks for implementation changes. Include regression cases for the Phase 1
probes; passing the old baseline alone does not cover the known defects.

This phase establishes a usable composition boundary. It does not claim that the complete new joint
solver, every target feature, every state path, or cache optimization has migrated. Those remain in
phases 3 through 6, with explicit handoffs rather than permanent duplicate implementations.
