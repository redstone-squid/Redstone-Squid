# Shared planning core boundaries and behavior

Status: complete as an investigation and architectural direction. The agreed decisions and
phase 2 handoff are recorded below. Production planner changes are not underway.

The direction is one core that owns planning decisions, with extensions for behavior that
actually depends on the target. The investigation covers the whole planning pipeline so
region pagination does not become another coordinator layered over independent planners.
See the [roadmap](README.md) for later implementation phases.

## Scope and working method

Trace current responsibilities and observable behavior before designing interfaces. Distinguish
actual target constraints, intentional application policies, and accidental differences between
implementations. Existing behavior is evidence; it is not automatically a requirement to preserve
all differences. Record compatibility implications when recommending a change.

Use Discord V2 and Slack messages to exercise constrained layouts, HTML to exercise a target
without a global message budget, and classic Discord to check the existing shared Discord
pipeline. Include other surfaces in the capability inventory where their rules differ.

Work in reviewable pieces. At a consequential decision, record the evidence, alternatives,
recommendation, and unresolved question, then pause for discussion. Focused experiments may
establish behavior, but should not quietly choose production interfaces or policies.

## Work sequence

1. Map the existing pipeline. Locate the owners of semantic interpretation, alternatives,
   composition, measurement, search, pagination, state, caching, diagnostics, and scene output.
   Identify which mechanisms are already shared and where one backend calls another.
2. Build a behavior matrix from representative documents. Separate target capabilities from
   content policies, and distinguish behavior observed in code from behavior confirmed by tests.
3. Compare architectural boundaries against those examples. Consider shared semantic planning,
   target-provided representations, and a hybrid. Determine what information composition and
   sound candidate pruning need before choosing a representation model.
4. Probe the unresolved assumptions with small experiments. Examine shared attachments,
   context-sensitive container costs, independent page turns, and target navigation. Use the
   experiments to test the proposed boundary, not to grow a second implementation.
5. Record the decisions and migration implications, and revise the roadmap where convergence
   of the existing planners changes the later work. Prepare a handoff sufficient to plan phase 2.

Each step may take more than one task. Keep the evidence and decisions here unless an experiment
needs a separate fixture or script that is useful to retain.

## Representative cases

| Case | What it needs to establish |
| --- | --- |
| Two paged siblings with unequal page sizes | Allocation stability, reachable page combinations, and when reflow is permitted. |
| A paged child inside a paged parent | Independent identity, hidden state, persistent content, and visible navigation. |
| A section split across pages | Repeated headings and container overhead, legal breaks, and inherited wrapper policies. |
| A complete compact alternative versus more pages | Ownership of representation preference, fidelity, and navigation tradeoffs. |
| Shared attachments across regions | Whether candidate summaries preserve enough identity for accurate composition and pruning. |
| Insertion, explicit navigation, and failed delivery | Reading-position reconciliation, application ownership, and staged publication. |
| The same region on HTML and a bounded message surface | Shared intent without inventing message limits for HTML. |

## Decisions to establish

- Which author policies have one meaning across targets, and where targets may introduce their
  own representation choices or degradation behavior.
- What structure the core needs to understand and what target-specific structure can remain opaque.
- What information targets must expose so composition and candidate pruning are sound.
- Which state belongs to layout, cursor position, application ownership, or frontend delivery.
- What evidence supports migrating each existing planning responsibility into the core.

Exact types, method signatures, search algorithms, and package boundaries remain open. Delivery,
authorization, data fetching, and application selection machines are surrounding systems to account
for, not automatic additions to the core's responsibilities.

## Validation and completion

Use focused planner and target-conformance tests to verify observed behavior. Small executable
examples should report target, authored policies, visible output, and degradation or failure.
For architectural experiments, identify what they demonstrate and what remains unproven.

Complete when the behavior and capability matrix, responsibility map, chosen direction, and
migration implications are supported by evidence, with remaining questions assigned to later
phases. Broad test counts alone do not establish the architecture. The next phase receives
concrete requirements and unresolved limits rather than a speculative API to implement verbatim.

## Progress

- [x] Map pipeline responsibilities and existing sharing in an initial code inventory.
- [x] Establish the representative behavior and target capability matrix.
- [x] Agree shared content-loss policy, direct target participation, and rich semantic planning.
- [x] Probe semantic preservation, resource composition, and state transitions.
- [x] Record the architectural direction and phase 2 handoff.

## Initial pipeline inventory

This is a code inventory, not a decision about the eventual boundaries. Paths below are
relative to `packages/squid-ui/src/squid_ui/planning/`.

| Responsibility | Current ownership | Implication for the investigation |
| --- | --- | --- |
| Request dispatch and frontend binding | `planner.plan_request` selects a whole backend or the region planner, disables caches for routed page factories, then binds handlers and prepares frontend forms. | A shared entry point exists, but it does not own the planning process. |
| Semantic interpretation and alternatives | Discord nominates strategies and fallback branches before lowering. HTML compiles semantics directly and explores fallbacks under authored budgets. Slack consumes that HTML result. | Targets currently differ in when alternatives remain available for selection. |
| Composition, measurement, and search | Discord evaluates lowered candidates through normalization, validation, and measurement. HTML handles local authored constraints. Slack applies text and block limits during conversion. Joint regions run another search around complete backend calls. | Target constraints can arrive after semantic choices have already been fixed. |
| Fragmentation and navigation | `balanced_breaks` serves HTML paging and Discord text/semantic paging. Discord dialects own legacy root pagination. Joint regions use another partition search. HTML and Discord construct different navigation bodies. | Some algorithms are already shared; their inputs, orchestration, and navigation construction are not. |
| Cursor reconciliation and staged state | Discord, HTML, and joint regions use `CursorCoordinator`; Slack forwards the HTML result's updates and pagers. | Preserve the existing ownership rules. Check that final target output still exposes the controls represented by those updates. |
| Caching and live bindings | Each backend controls cache replay and binding reconstruction. Slack has an outer cache around an HTML compilation. Joint planning disables backend caches for its probes. | Reuse needs one consistent lifecycle without retaining stale callbacks or routes. |
| Diagnostics and output | Discord reports semantic events and measured degradation; HTML and Slack report compilation events. Each backend constructs its final scene. | Shared decisions need comparable effects; final scene construction can remain target-specific. |

The Discord V2 and classic dialects already share `DiscordPlanner` and provide operations for
normalization, validation, root pagination, and scene construction. This is useful precedent,
but its intermediate primitives and measurement machinery remain Discord-shaped.

Source entry points:
[dispatcher](../../../packages/squid-ui/src/squid_ui/planning/planner.py),
[Discord](../../../packages/squid-ui/src/squid_ui/planning/discord_planner.py),
[HTML](../../../packages/squid-ui/src/squid_ui/planning/html_planner.py),
[Slack](../../../packages/squid-ui/src/squid_ui/planning/slack_planner.py), and
[joint regions](../../../packages/squid-ui/src/squid_ui/planning/regions.py).

## Initial target matrix

These are properties of the current code. They are not a new support contract or a claim
that every node placement has been tested.

| Target | Constraints and structure | Current planning path |
| --- | --- | --- |
| Discord V2 | Message resource budgets plus component and nesting constraints. | Shared Discord compiler; joint regions are diverted to the V2-specific region solver. |
| Discord classic | Content, embed, and control constraints with a different message body. | Same Discord compiler, with classic normalization, validation, pagination, and scene assembly. |
| HTML | No global reservable resource axes; authored budgets and paging still apply. | Direct semantic compilation; authored paging uses the shared break and cursor helpers. |
| Slack message and Home | Global block capacity and local text/control constraints. | HTML intermediate followed by Slack conversion and limit enforcement. |
| Slack modal | Block limits and form-specific structure; requires one top-level form trigger. | HTML intermediate plus modal-specific conversion and validation. |

Joint regions currently reject non-V2 bodies before any of these backend paths run. That
restriction is an implementation gap, not evidence of target capability. The executable
comparison below concerns alternatives and content loss without any region nodes. The final
representative behavior matrix follows the agreed decisions.

## Experiment 1 Alternatives under target pressure

Input: two paragraphs, `Length: 10 blocks` and `Width: 5 blocks`. An authored alternative
contains both dimensions in one paragraph: `10 × 5 blocks`. Reserve all but one component on
V2 and all but one block on Slack messages. These reduced limits are test conditions, not
claims about the services' normal limits. Give HTML no global reservation.

Observed through the public `sl.planning.plan` entry point:

| Input and mode | Discord V2 | Slack message | HTML |
| --- | --- | --- | --- |
| Two paragraphs, non-strict | Fails with `UnsolvableLayoutError`. | Shows only the length paragraph and reports `slack.blocks.truncated`. | Shows both paragraphs. |
| Authored alternative, non-strict | Selects `10 × 5 blocks` and reports a semantic fallback. | Still shows only the length paragraph and reports block truncation. | Shows both original paragraphs. |
| Authored alternative, strict | Rejects the selected fallback with `LayoutDegradedError`. | Rejects block truncation with `LayoutDegradedError`. | Shows both original paragraphs. |
| Authored alternative inside a 13-character budget | Not exercised in this comparison. | Not exercised in this comparison. | Selects the compact alternative in non-strict mode; strict mode rejects the fallback. |

The Slack observation is about visible blocks. Its separate accessibility fallback text may
still contain both original paragraphs; retaining that text does not make the visible layout
use the supplied complete alternative.

Reproduce the constrained alternative case from the repository root with `uv run --no-sync python`
and the following input. `scene.Codec.dumps` exposes the result body and the diagnostic codes
show which operation was selected.

```python
import squid_ui as sl
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.target_types import DiscordAdapter, SlackAdapter

content = sl.fallback(
    sl.stack(sl.paragraph("Length: 10 blocks"), sl.paragraph("Width: 5 blocks")),
    sl.paragraph("10 × 5 blocks"),
)
v2 = components_v2_target(AdapterProfile(DiscordAdapter, "probe.discord", "1"))
slack = sl.slack.message_target(adapter=AdapterProfile(SlackAdapter, "probe.slack", "1"))
for target in (
    v2.reserve(ResourceCost({Axis.COMPONENTS: 39})),
    slack.reserve(ResourceCost({Axis.BLOCKS: 49})),
    sl.html.target(),
):
    result = sl.planning.plan(content, target=target)
    print(target.id, sl.scene.Codec.dumps(result.scene))
    print([event.code for event in result.report.events])
```

The source explains the difference: HTML's ordinary `FallbackContent` branch compiles only
its primary. Slack then applies block limits to that completed intermediate and never sees
the alternative. Its `_Lowerer.blocks` slices excess blocks even without an authored omission
policy. Discord evaluates fallback branches while checking target feasibility.

Validation: the focused HTML, Slack, and portable target-conformance suites passed, with
266 tests. The comparison also exercised strict mode and HTML's authored budget directly.
The existing Slack reservation test explicitly expects omission in non-strict mode; changing
this behavior is a compatibility decision, not just moving code between modules.

## Decision 1 Who authorizes content loss

Status: agreed. Permission to truncate or omit content belongs to shared policy and requires
an authored or explicit request choice. Targets describe valid representations, limits, and
effects; the core chooses among permitted candidates. Target conversion cannot introduce
additional unapproved loss after selection.

This changes Slack's current non-strict defaults. Preserve permissive fitting through an explicit
policy where callers need it, with migration details to be designed in the implementation phase.
Keeping different implicit loss permissions for each target was considered and not selected.

Decision 4 below establishes how strict mode treats equivalent representations. The experiment
shows that current strict mode rejects even the complete compact alternative on V2 and under
an HTML budget. The metadata for declaring equivalence remains part of the phase 2 design.

## Decision 2 Slack must participate directly in shared planning

Status: agreed direction. Replace the implicit path through the HTML planner with Slack-specific
participation in the shared core. HTML and Slack are peer targets; a completed HTML scene is not
the shared planning representation. Slack constraints and representation choices must be available
before the core selects content, page boundaries, and visible controls.

The current path reuses more than HTML formatting: it resolves semantic choices, paging, state,
and handlers before Slack conversion. `_Lowerer.node_blocks` then reconstructs meaning from HTML
tags, classes such as `squid-gallery` and `squid-actions`, and attributes. For modal forms it also
returns to the original semantic form schema. These are evidence that the HTML scene is carrying
responsibilities beyond a target output format.

The replacement should retain reusable mechanisms, including text parsing, identity, cursor
reconciliation, and binding management, in appropriate shared code. It should not duplicate all
of those mechanisms in a new standalone Slack compiler. Target-specific formatting and final
scene construction remain extension responsibilities.

The initial responsibility map for the replacement is:

| Responsibility | Intended owner |
| --- | --- |
| Authored structure, identity, and permission to change content | Shared core. |
| Valid representations, resource and nesting constraints, and conversion effects | Target extensions, available to shared planning before selection. |
| Representation selection, allocation, fragmentation, and state reconciliation | Shared core, with target-specific constraints. |
| Final scene construction and target validation | Target extensions; construction must honor the selected plan. |

This establishes ownership. Decision 3 below chooses the working direction for the planning
model; concrete interfaces still require evidence from composition and state experiments.

No production behavior has changed. The experiments and final handoff below establish the
requirements for the next implementation phase.


## Decision 3 Preserve decision-relevant semantics in the core

Status: agreed working direction. The core should understand content structure, author policies,
interaction identity, composition dependencies, and representation choices throughout planning.
Target payloads may remain opaque only when all behavior relevant to a planning decision is exposed.

Start from the existing authored semantic vocabulary. Investigate how to preserve its information
and associate planning decisions with it before inventing a parallel vocabulary. Targets contribute
realizations, costs, constraints, and effects while choices remain open. The core must be able to
consider representation changes and fragmentation together; neither target scene assembly nor an
opaque leaf compiler should settle those choices prematurely.

A rich semantic model does not require the core to know target output fields or perform frontend
rendering. It requires enough information to distinguish meaningful units, legal transformations,
resource sharing, and interactions. The composition experiments establish initial information requirements
for tables, shared resources, and nested regions. Exact types and extension APIs remain open.


## Composition findings and fidelity policy

The [composition experiments](composition-experiments.md) probe tables, shared attachments,
and the existing nested-region behavior. They establish requirements for semantic identity,
content effects, contextual resource accounting, and state reconciliation. They also record
current defects that migration must address. The behavior matrix and architecture handoff
below combine those findings with the agreed policies.

### Decision 4 Separate fidelity from representation preference

Status: agreed. Strict planning preserves content, relationships, and interaction behavior,
and enforces authored hard requirements. It permits equivalent representations and lossless
pagination; moving away from a preferred representation is not itself a strict failure.

The core needs to distinguish a different presentation of the same content from a representation
that omits or changes content or behavior. Existing Discord strategies already make some exact
presentation changes without degradation. Authored `fallback` branches, however, always receive
a semantic-loss penalty, and strict mode rejects them even when an author intended an equivalent
compact form. Slack's table slicing demonstrates the opposite problem: actual visible content
loss can go unreported.

The agreed policy is:

- Treat content and behavior preservation separately from representation preference and navigation
  effort. Exact representation changes and lossless pagination can satisfy strict planning.
- Continue to enforce authored hard requirements independently. Preserving all cells does not
  authorize ignoring a requirement to retain a particular structure or interaction model.
- For built-in transformations, establish preservation through their contract and tests, with
  semantic identities and relationships available for validation.
- For arbitrary authored alternatives, do not infer equivalence from text or similar tree shapes.
  Let authors explicitly declare equivalent alternatives; conservatively classify undeclared
  replacements. The detailed classification and declaration API remain for the implementation plan.
- Keep opted-in content loss available to non-strict planning. Strict mode
  still refuses actual loss, even when an author supplied a permissive adaptation.

Representation preference belongs in candidate ranking, while fidelity and hard requirements
determine admissibility. The precise ranking and representation metadata remain to be designed.
No production behavior has changed.


## Representative behavior matrix and state findings

This matrix covers the cases needed to establish the planning boundary. It is not an exhaustive
cross-product of semantic nodes, parameters, targets, and frontends; that conformance work belongs
to migration and target completion.

| Case | Current evidence | Requirement for the shared core |
| --- | --- | --- |
| Independently paged siblings | Region tests visit every coordinate of a small sibling pair and check allocations remain stable. | Cursor-only movement reuses the committed page layout; one region does not unexpectedly repartition its sibling. |
| Nested regions and persistent content | Tests cover hidden child cursors, visible bindings, repeated headers/footers, and section shells. | Retain logical positions while content remains in the tree; account for persistent content and navigation on every reachable page. |
| Legal breaks and container policies | Region tests cover `unbreakable` and `keep_with_next`; the sequence breaker and HTML paging test soft fill preferences. The joint legacy conversion omits `min_fill` and `widows`. | Preserve hard break rules and keep soft preferences distinct; carry wrapper context through transformations instead of silently dropping it. |
| Complete alternative under pressure | Experiment 1 distinguishes Discord selection, Slack post-conversion omission, and unbounded HTML behavior. | Consider permitted representations against the actual target constraints while choices remain open. |
| Structured content and shared resources | Composition probes expose lost table information, unreported table slicing, and double-counted shared attachments. | Preserve semantic units, relationships, effects, and resource identity through composition and selection. |
| Content insertion or target change | A direct state probe preserved the surviving anchor after insertion and after a capacity change. | Permit replanning when relevant inputs change, then reconcile reading position through stable semantic identity. |
| Explicit navigation or missing anchor | The probe confirmed a requested page overrides the remembered anchor; removing the anchor reset to page zero under the existing policy. | Preserve the established position policy. A future change to nearest-survivor behavior would be a separate UX decision. |
| Controlled and routed ownership | Existing tests check application values, absence of managed cursor writes, and generated routes. | Planning must not take ownership of application state or invoke change handlers. |
| Failed delivery and stale controls | Mounted tests cover failed edit, retry, sibling state, and generation checks. | Return proposed state and bindings; the frontend retains authority over successful publication and interaction admission. |
| HTML without message limits | HTML keeps the preferred full representation unless authored constraints apply. | An unbounded target remains valid; absence of global limits must not invent a pagination requirement. |

The additional state probe planned seven keyed paragraphs in a region with a six-component target.
Planning the initial requested page left the session empty until its updates were applied. A later
page request proposed a different cursor while keeping the committed allocation and cursor intact.
Insertion preserved the old anchor; removal of that anchor used the current first-page fallback.
Increasing capacity allowed a single page while the proposed report still identified the old anchor.
This last observation does not assert that inactive pager state is retained after committing a
single-page result; cursor collection is a separate part of the existing lifecycle.

Validation: 101 cursor identity, navigation, region, pagination, and ownership tests passed. The
direct probe exercised candidate state before applying updates as well as the content/target-change
cases. The underlying precedence policy is in `pages.resolve_page_position`; `CursorCoordinator`
applies it to a selected partition, and `PresentationState.apply_updates` commits proposed state.

The shared coordinator still constructs Discord primitive navigation in its `controls` method.
Its generic position resolution and state logic can be retained, while control realization must
move to target-specific participation. Source pagination and selection windows already share some
navigation vocabulary, but their data ownership remains outside layout fragmentation.

## Architectural direction

Use one planning core that retains the authored semantic information and owns the decisions that
change its presentation. Targets contribute possible realizations and their constraints while
those decisions remain open. A complete target scene is an output, not the common planning input.

The conceptual responsibilities are:

1. Establish semantic occurrences, identity, inherited policies, and application-owned state from
   the authored graph. Preserve the original content and relationships as choices are explored.
2. Obtain target realizations and their effects, costs, contextual constraints, and resource
   dependencies. A realization may need target-specific construction to measure it; this must not
   erase the semantic information or secretly select additional adaptations.
3. Coordinate representation choices, composition, and fragmentation in one search. Apply shared
   permission and strictness rules; compare admissible candidates using the eventual ranking policy.
4. Reconcile positions against the selected page structure and collect the visible interactions,
   resources, diagnostics, and proposed presentation updates.
5. Build and validate the target scene from the selected result. Scene construction cannot silently
   truncate, omit, or otherwise revise a decision to make it fit.

These are responsibilities and dependencies, not a mandated sequence of whole-tree passes. Search,
measurement, and composition may need to interact incrementally. The next phase must establish the
contract that makes those interactions correct and bounded.

Keep content preservation, hard requirements, representation preference, and resource consumption
separate in the planning model. Equal resource vectors do not imply equivalent candidates. A
preservation claim must include relationships and interaction behavior, not only retained strings.

The existing semantic nodes are the starting point. Planning may annotate occurrences or derive a
richer graph, but should not create a competing authored vocabulary merely to unify the backends.
An opaque native extension can remain atomic only if its placement constraints, resources, and
other decision-relevant behavior are exposed. Missing information must limit the transformations
available to the core rather than invite guesses about the extension's contents.

Preserve reusable mechanisms where their contracts fit: cursor precedence and ownership, exact
sequence breaking, localization and text parsing, staged state publication, and separation of
serializable scenes from live bindings. Do not carry Discord primitive assumptions into the shared
contracts simply because these mechanisms currently reside beside them.

## Phase 2 handoff

The next task is a separate detailed plan for establishing composition across targets. Its scope
includes the first usable core/target boundary, rather than a utility that merely concatenates
independently completed scenes.

The plan needs to resolve:

- How authored semantics and planning decisions are represented and related, including occurrence
  identity and inherited policies. Choose concrete types only after tracing these requirements.
- How targets expose alternatives, semantic effects, legal breaks, continuation requirements,
  measurements, and context-dependent composition constraints without owning another complete search.
- How composition preserves resource sharing and what comparisons are safe before full context is
  known. Begin with the shared-attachment counterexample, not scalar costs alone.
- How preservation is declared and checked for built-in realizations, arbitrary authored alternatives,
  and native extensions. Decide the exact metadata and author API for equivalent alternatives.
- How target participation reports failures and consumes shared work budgets, leaving the full
  allocation algorithm and ranking policy to phase 3.
- How existing callers remain usable while responsibilities move into the core. Temporary bridges
  must have explicit removal points; no new implicit Slack-to-HTML planning path should be introduced.

Exercise the proposed boundary on V2 and Slack messages, with HTML checking that an unbounded target
remains natural. Include a table, alternative representations, shared attachments, and a repeated
container/navigation cost. Target families may migrate incrementally; phase 6 completes their
coverage, while phases 3 through 5 supply the joint search, state integration, and reuse machinery.

Migration must address the concrete gaps recorded here: selective wrapper handling, lost table
identity and display policies, unreported table slicing, implicit Slack content loss, attachment
accounting, unconditional fallback degradation, duplicate planning paths, and inconsistent work
metrics. These are acceptance cases for the relevant phase, not permission to expand phase 1 into
an unplanned implementation.

No further Phase 1 policy choice is blocking the next plan. Exact interfaces, ranking, optimization,
and compatibility mechanics remain deliberate work for the subsequent phase plans. Production
behavior has not changed during this investigation.
