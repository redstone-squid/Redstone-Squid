# Shared planning core boundaries and behavior

Status: in progress. This phase investigates and designs the shared core; architectural
choices are discussed as they arise. Production planner changes are not underway.

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
- [ ] Establish the behavior and capability matrix.
- [ ] Discuss the shared policy and target extension boundary.
- [ ] Probe composition and state assumptions.
- [ ] Record the architectural direction and phase 2 handoff.

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
restriction is an implementation gap, not evidence of target capability. The full behavior
matrix remains unfinished; the first executable comparison below concerns alternatives and
content loss, without any region nodes.

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

This does not decide whether selecting an authored alternative should count as degradation,
which alternatives are equivalent, or how strict mode should treat them. The experiment shows
that strict mode currently rejects even the complete compact alternative on V2 and under an
HTML budget. Those fidelity questions remain open.

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

This is an ownership direction, not a chosen intermediate representation or interface. The next
investigation is what information must survive between authored semantics and target realization,
including alternatives, containers, resources, and interaction bindings. Compare a shared semantic
planning model with target-owned fragments before fixing their boundary.

No production behavior has changed. Composition experiments, the full matrix, fidelity policy,
and the final architectural recommendation remain pending.
