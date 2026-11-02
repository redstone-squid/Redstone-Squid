# 08 — Schematic Rendering and Simulation Orchestration

## Findings

- `prepare_render()` still collapses disabled, missing, unsanitized, poisoned, oversized, missing-file, fresh, and cached states into `None` or a prepared value. The durable worker therefore cannot distinguish a permanent skip from an absent attachment, and UI surfaces cannot explain the outcome.
- Operational render failures now propagate to the durable queue, rendering is durably retried/dead-lettered, projections are fenced to the current primary schematic, and the old unbounded `_render_attempted` set is gone. These concerns are already fixed.
- `prepare_render()` remains long and mixes eligibility, capability/resource acquisition, cache lookup, native rendering, output validation, and persistence handoff. Split these stages around a typed outcome rather than into incidental helpers.
- Simulation errors for ambiguous inputs tell moderators to pass coordinates but omit the candidate coordinates. `SimulationResult` has grown into a broader run/evidence report; rename it only if a better name (`SimulationEvidence` or `TimingSimulationEvidence`) improves persisted and UI language consistently.
- Worker stderr tasks still use bare `asyncio.create_task`. Move their ownership into an anyio task group or the process/runtime supervisor while retaining asyncio streams, locks, queues, and semaphores.
- Repeated poison/crash handling in render and simulation should be one service helper with operation-specific policy; durable retries must remain authoritative for render failures.

## Plan

1. Introduce a render preparation outcome with explicit states such as `ready`, `cached`, and permanent `skipped(reason)`. Keep transient failures as typed exceptions so the durable queue retries them.
2. Extract eligibility evaluation, render recipe/cache resolution, and native execution/output validation. Have the projector acknowledge permanent skips and retry/dead-letter operational failures.
3. Expose safe skip reasons to moderator/UI callers (disabled, no primary, not sanitized, over block/volume budget, poisoned/missing artifact) without leaking engine internals.
4. Return ambiguous simulation input coordinates as structured public context and render them in `/build measure-timing`; validate a manual coordinate against the candidates.
5. Consolidate worker-crash poisoning and revise simulation evidence naming only across the complete domain/wire/persistence/UI path.
6. Give each stderr pump an anyio owner tied to its worker/pool lifetime; preserve orderly pipe draining during crash and shutdown.

## Interfaces and tests

- Likely additions: `RenderPreparation` plus a `RenderSkipReason` enum; structured simulation-input candidates in `InvalidSchematicError.public_context`; optionally `SimulationEvidence` as a coordinated rename.
- Unit-test every render outcome, retry classification, cache hit, invalid PNG, stale primary, and safe reason rendering.
- Test ambiguous/no/manual simulation inputs and localized Discord output listing candidate coordinates.
- Test stderr-pump ownership, cancellation, crash draining, respawn, and pool shutdown under anyio on pytest-asyncio's asyncio backend.
- Integration-test durable queue acknowledgement for permanent skips and retries/dead-lettering for transient failures.

## Disposition

- **Fix:** typed render outcomes, service decomposition, candidate-coordinate UX, shared crash policy, task ownership.
- **Already fixed:** bounded render attempts via durable work, failure propagation, primary fencing, configurable render/simulation timeouts.
- **Investigate before change:** renaming `SimulationResult`; retain it if a broader name adds churn without clearer UI/domain meaning.
