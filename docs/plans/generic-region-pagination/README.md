# Generic region pagination roadmap

Status: phases 1 and 2 complete. Phase 3 joint search is the next task and needs its own
detailed plan.

Make joint region pagination part of the generic planning architecture. Sibling and nested
regions should share target limits while preserving content, navigation, and reading position.
The shared machinery must work with different target representations and resource rules.

This roadmap sets outcomes and an initial sequence. It leaves representation types, backend
interfaces, solver algorithms, and migration mechanics to the phase plans. Findings from one
phase may change the scope or order of later phases.

## Starting point

The current implementation provides `sl.region`, resource-frontier allocation, keyed anchors,
and staged presentation updates. It also contains assumptions that prevent it from serving as
the generic foundation:

- Scene assembly directly uses Components V2 children and panels.
- Composition adds resource vectors, although resources such as shared attachments need
  more information to compose accurately.
- Representation discovery repeatedly invokes a complete backend with reduced budgets.
- Wrapper support is selective; the conversion of legacy `Paged` content omits policies such
  as `min_fill` and `widows`.
- Region search and backend search account for work differently, and region planning bypasses
  the existing plan caches.
- Legacy root pagination and joint regions follow separate planning paths.

Existing tests cover useful allocation and delivery behavior. They are regression evidence
for the implementation, not proof of a general composition or search contract. Reuse the
working behavior and tests where they fit; the current internal structure is open to replacement.

Start from `planning/regions.py`, `planning/planner.py`, `planning/dialect.py`, and the target
backends in `packages/squid-ui/src/squid_ui/`. The [architecture guide](../../squid-ui-architecture.md)
and [pagination ownership contract](../pagination-ownership.md) describe surrounding behavior.

## How to execute the phases

Before implementing a phase, create a separate numbered plan beside this roadmap. That plan
should establish the current evidence, decisions and alternatives, scope, dependencies,
reviewable implementation steps, and validation needed to finish the task. Write these plans
when the phase begins, using the findings already available; do not prefill all of them now.

Each phase ends with a validated outcome and a short handoff recording decisions, remaining
limitations, and any changes to this roadmap. For a design phase, that outcome is evidence and
decisions; implementation phases must leave working changes. A phase may be split into smaller tasks
when its detailed planning exposes independent work. Tests belong in every phase; the final
phase checks the assembled system rather than postponing correctness work until the end.

## Phase 1 Establish a shared planning core

Completed investigation: [Shared core boundaries and behavior](01-shared-core.md), including
the agreed decisions, evidence, and phase 2 handoff.

Inventory the whole planning pipeline across targets, including semantic interpretation,
representation selection, composition, measurement, search, pagination, state reconciliation,
caching, diagnostics, and scene construction. Identify duplicated responsibilities and actual
target differences. The direction is one planning core with explicit target extensions;
joint pagination belongs inside that core. Content-loss permission is shared policy, and Slack
will participate directly rather than compile through the HTML target first. The working direction
is a rich semantic planning model that retains everything relevant to planning decisions, with
target-specific realizations.

Use representative examples to establish shared behavior and the information targets must
supply. Include persistent content, nested containers, shared resources, alternatives,
application-owned pages, and targets without a global message budget. Compare possible
boundaries without prescribing an intermediate representation or extension protocol.

Complete when there is a behavior and capability matrix, a responsibility map supported by
examples or focused experiments, and enough evidence to plan the next phase. Public APIs may
evolve with an explicit migration decision. Stop at consequential design choices to discuss
the alternatives before treating them as settled; this phase may span several tasks.

## Phase 2 Establish composition across targets

Implementation plan: [Shared composition and target participation](02-composition.md), with
a [working API sketch](api-sketch.md).

Implement the boundary through which shared planning can obtain representations, compose
content and navigation, and account for target constraints. Cover container context and
resources whose costs cannot safely be summed. Establish what information candidate comparison
and pruning require, and what the backend can guarantee about the alternatives it exposes.

Exercise the design with at least two materially different existing targets. Choose them in
the phase plan to challenge the abstraction, rather than implementing only V2 and assuming the
other targets will fit later. This need not complete every target's pagination integration.

Complete when shared planning can compose and validate representative content on those targets
without inspecting their concrete scene bodies, with explicit cost and capability contracts.

Depends on phase 1.

## Phase 3 Build the shared allocation search

Implement joint allocation using the established contracts. Account for persistent content,
navigation, legal breaks, nested regions, and authored alternatives. Determine the optimization
policy and the conditions under which pruning is sound. Distinguish a valid result, established
infeasibility, and an exhausted search; state any limits on optimality or completeness precisely.

Give planning a coherent work budget across shared and backend operations. Validate small cases
against exhaustive solutions and exercise competing resource limits and composition effects.
The detailed plan should choose the algorithm from these requirements and the phase 2 evidence.

Complete when the solver has tested correctness properties and bounded work, and target-specific
scene construction remains outside it.

Depends on phase 2.

## Phase 4 Integrate pagination and presentation state

Bring root pagination, explicit regions, and existing layout-driven local pagers onto the shared
foundation. Preserve authored policies through wrappers and containers, or make any intentional
behavior change explicit with a migration. Keep data fetching and application-owned selection
windows distinct from layout fragmentation unless the detailed plan establishes a reason to
change that boundary.

Integrate identity, anchor reconciliation, hidden regions, stable allocations, and controlled,
uncontrolled, and routed pages. Preserve the delivery boundary: planning stages presentation
changes, and failed delivery cannot publish unseen controls or advance committed layout state.

Complete when the supported pagination paths use the shared machinery, compatibility decisions
are documented, and interaction tests cover page turns, content changes, retries, and stale input.

Depends on phase 3; compatibility findings may require revisiting earlier contracts.

## Phase 5 Integrate reuse diagnostics and performance

Make caching, invalidation, budget accounting, and diagnostics agree with the new planning model.
Distinguish changes to content or layout constraints from cursor-only movement without reusing
stale handlers or route encodings. Reports should explain allocations and failures, and metrics
should describe the work actually performed.

Measure representative workloads, including many items, nested regions, multiple resource limits,
and repeated page turns. Choose performance targets and optimizations in the detailed plan;
optimizations must preserve the established correctness and search guarantees.

Complete when reuse is covered by correctness tests, search costs are observable, and measured
workloads meet the agreed performance expectations.

Depends on phases 3 and 4. Earlier phases should already expose the information needed here.

## Phase 6 Complete target coverage and migration

Finish integration for the existing target families identified in phase 1. Validate shared
behavior through conformance tests while respecting actual target capabilities. Unsupported
combinations must have a concrete capability reason rather than inheriting a blanket V2-only
restriction. Revisit the capability matrix against the finished implementation.

Exercise representative application screens, including crowded build-browser and help states.
Migrate callers where useful, remove superseded planning paths and temporary compatibility code,
and update the public documentation to match the final behavior.

Complete when target coverage, application behavior, documentation, and removal of obsolete
machinery have been verified. Record any consciously deferred capability with its reason;
passing one target's tests is not completion of the generic system.

Depends on phases 4 and 5. Target integration can begin earlier where contracts are stable.

## Completion criteria

- Shared allocation does not construct or inspect a concrete target's scene body.
- Composition and search have explicit, tested contracts for resources and authored policies.
- Pagination paths share a foundation while state ownership and delivery guarantees remain clear.
- Search limits, diagnostics, and reuse have consistent semantics.
- Cross-target tests and application scenarios support the documented behavior.
- Remaining limitations are deliberate and documented; obsolete parallel machinery is removed.

Phase 1 established shared content-loss policy, fidelity-based strictness, a rich semantic core,
and direct target participation. Phase 2 supplies an opt-in composition entry point, peer alternatives over shared semantic content,
structural resource budgets, live bindings, and typed atomic extension contracts. Production changes follow the relevant phase plans; an open design
question is not an implementation decision.
