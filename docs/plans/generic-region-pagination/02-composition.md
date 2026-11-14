# Shared composition and target participation

Status: implementation in progress. The first slice adds peer alternative declarations,
scoped semantic references, and structural correspondence checks. These remain below the
public planning entry point while the composition contract is built. The [API sketch](api-sketch.md)
records the proposed author syntax and target contract.

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

The concrete effect vocabulary and how declarations refer to semantic units remain design work.
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
shortening returns a candidate failure. Inherited layout requirements and strict admission still
need contextual composition evaluation; these checks alone do not certify a valid plan.

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

Run focused tests with `--no-cov` during development and after final changes, including the affected
semantic, target-conformance, region, and frontend integration suites. Run whole-project type checking
and relevant lint/format hooks for implementation changes. Include regression cases for the Phase 1
probes; passing the old baseline alone does not cover the known defects.

This phase establishes a usable composition boundary. It does not claim that the complete new joint
solver, every target feature, every state path, or cache optimization has migrated. Those remain in
phases 3 through 6, with explicit handoffs rather than permanent duplicate implementations.
