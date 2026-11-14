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

- [ ] Map pipeline responsibilities and existing sharing.
- [ ] Establish the behavior and capability matrix.
- [ ] Discuss the shared policy and target extension boundary.
- [ ] Probe composition and state assumptions.
- [ ] Record the architectural direction and phase 2 handoff.
