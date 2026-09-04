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

## Implementation notes

Landed across `a94106d9`…`bec8e143`. (These commits were renumbered by a later rebase; the original
citation — `74c17dea`…`47e6f053` — no longer resolves in history, but the four commits below carry
the same messages and diffs described here: `a94106d9` "schematics: type the render preparation
outcome", `ecdde5e9` "schematics: tell moderators why a build has no preview", `0d322812`
"schematics: name the inputs an ambiguous simulation could use", `0c6304e9` "schematics: give every
worker stderr pump an owner", `bec8e143` "docs: name pyrefly as the local type checker" — the last of
which is also where the `TaskGroup.start_soon` typing fix below actually landed.) Four things the
plan did not anticipate:

- **The candidate coordinates could not have reached a caller at all.** `_translate` rebuilds a
  child's `invalid` error from its wire kind alone, discarding the message, the end-user action,
  and the context — so the "run the command again with input_position" refusal never left the
  supervisor intact either. That discarding is deliberate (nothing the engine writes should reach
  a user), so the fix was a fourth wire kind, `ambiguous_simulation_input`, that the supervisor
  reconstructs from the coordinates rather than from a string. `AmbiguousSimulationInputError`
  subclasses `InvalidSchematicError` and keeps `ErrorCode.SCHEMATIC_INVALID`, so
  `contracts/openapi.json` is untouched.
- **A named input coordinate was being ignored, not just under-explained.** `_resolve_simulation_input`
  checked the Insign annotation and the sole-control heuristic *before* `manual`, so passing
  coordinates for the second of two buttons silently timed the first and reported the result as
  the answer. The manual coordinate is now checked first. Covered by two new integration tests
  against the real engine.
- **`running()` cannot be held by a pytest-asyncio fixture.** Setup and finalization run in
  different tasks and an anyio task group can only be exited by the task that entered it, so the
  integration fixture holds the lifetime in an owner task of its own. Unit tests that only drive a
  pump directly use an `unowned_worker()` helper whose spawn callable refuses.
- **`SimulationResult` is retained.** Checked the whole path before deciding: the persisted column
  (`schematics.simulation_evidence`), the read-model field (`StoredSchematic.simulation_evidence`),
  and the Discord card ("Simulated timing evidence", "Moderator evidence only") already say
  "evidence", and the type itself really is one run's result. Renaming it touches thirteen modules
  and the wire encoder names to make the vocabulary no clearer than it already is.

The plan's step 3 asked for skip reasons on "moderator/UI callers" without naming a surface;
`prepare_render` cannot be one, because asking it costs a GPU render. `explain_render_skip` answers
from configuration and the stored analysis alone and is rendered on `/build schematic info`, which
is where a moderator already asks what the bot knows about a build.

New user-facing strings are not in `locales/squid.pot`. That catalog is already ~40 msgids behind
HEAD from earlier plans in this series, so refreshing it is its own commit rather than a partial
sweep hidden inside this one.

## Status

**Done.** Re-verified against current code: `RenderPreparation`/`RenderSkipReason`/`FreshRender`/
`CachedRender`/`SkippedRender` all exist in `squid/schematics/application/queries.py`, the old
unbounded `_render_attempted` set is gone, `explain_render_skip` is wired into
`squid/bot/submission/schematics.py:78`, `AmbiguousSimulationInputError` and the
`ambiguous_simulation_input` wire kind exist end to end, `_resolve_simulation_input` checks `manual`
before Insign/heuristic, `SimulationResult` was retained rather than renamed, and stderr pumps are
owned by the pool's `anyio.create_task_group()` (`worker.py:431`, `self._pumps.start_soon`) rather
than bare `asyncio.create_task`. Only the implementation-notes commit hashes were stale (a later
rebase renumbered them); corrected above.

## Validation

- Focused, with `--no-cov`: `tests/unit/schematics/`, `tests/unit/worker/`,
  `tests/unit/bot/submission/test_schematic_commands.py`,
  `tests/integration/schematics/test_worker_pool.py` (against the installed engine).
- Full `tests/unit/` once after the task-ownership change, since the pool's lifetime is process-wide:
  1977 passed.
- Changed-file Ruff via the commit hooks, plus `git diff --check`. No persistence structure changes,
  so no `alembic heads` run.
- `just typecheck`: 17 errors, all in `tests/*/accounts/` and `tests/unit/persistence/` from
  `ee0d2ec1`, none in files this plan touched. The two it did find here are fixed in `bec8e143`
  (cited as `8f715f74` before the rebase noted above): `TaskGroup.start_soon` wants a callable
  returning a `Coroutine`, not an `Awaitable` — confirmed still in place at
  `squid/schematics/infrastructure/worker.py:124`.

## Completion update (2026-08-30)

**Done.** Typed preparation outcomes, durable retries, dead letters, current-primary fencing, and
simulation evidence remain intact. `47b53ebb` adds PostgreSQL scenarios for permanent-skip
acknowledgement and transient retry/backoff/dead-letter state, and moves all schematic integration
task ownership to anyio. The 22 real worker-pool cases pass locally; the new PostgreSQL scenarios
are present but execution is externally gated by Docker access.
