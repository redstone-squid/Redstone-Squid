# Discord runtime simplification

Status: implementing. Baseline lifecycle tests pass; ownership audit completed.

Simplify `squid-ui-discord` so opening a screen, handling an interaction, and closing a
message can each be understood through a small sequence of operations with explicit owners.
Preserve public behavior, delivery guarantees, and optional durability. Judge progress by
removed state and coordination, rather than smaller files alone.

## Scope and evidence

The main work is in `packages/squid-ui-discord/src/squid_ui_discord/`:

- `message_root.py` coordinates candidate preparation, delivery, interaction admission,
  action execution, challenges, refresh scheduling, and closure. It is about 3,100 lines.
- `message_root_lifecycle.py` already owns candidate settlement and published bindings.
  Strengthen this existing boundary before introducing another rendering abstraction.
- `_invoke` and `_invoke_submit` independently manage acknowledgement watchdogs and task
  groups. Their flush paths also coordinate action results and presentation, with meaningful
  differences for form validation and busy indicators that must remain explicit.
- `Screen._screen_bind` directly updates the root's `_shutdown_dependencies` and registers
  callbacks on `_shutdown`. `_screen_opened` appends a private finishing hook. This makes
  screen code responsible for maintaining root shutdown invariants.
- Screens track parent/child relationships, sessions track root attachment relationships,
  and the runtime tracks roots by scope. These relationships serve different purposes;
  the audit must establish which cleanup bookkeeping is actually redundant.
- `Scope._deliver` handles both sessionless roots and session admission. Session opening
  indexes the root before rendering, delivers it, performs optional durable registration
  work, registers the newcomer, and retires incumbents. Those ordering constraints matter.

Use [the architecture guide](../squid-ui-architecture.md) as the behavior reference and
update its affected sections with implementation changes. This work does not reopen the
portable planner, reactivity transaction model, storage format, or target design. Public API
reductions and easier runtime startup are separate follow-up work.

## Guarantees to preserve

| Boundary | Required behavior |
| --- | --- |
| Application state and delivery | A committed action and a successful message edit are distinct events. Failed delivery must preserve the existing recovery behavior. |
| Candidate settlement | Every staged candidate is committed or discarded once, including cancellation. Failed delivery cannot publish undelivered controls. |
| Unchanged rendering | Bindings and application state can advance without another edit or a new visible generation. |
| Interaction authority | Access, stale-generation policy, action concurrency, and acknowledgement timing retain their current behavior. Child results receive fresh parent action authority. |
| Screen completion | Results are staged until action commit. A failed parent result callback leaves the child available for retry. |
| Closure | The first accepted close fixes its reason and options. Cancelling a waiter does not cancel accepted cleanup. Cleanup failures remain observable and do not skip remaining cleanup. |
| Ownership | Parent or scope closure ends owned work. Completed children are released after cleanup, and shutdown cannot wait on itself. |
| Admission and durability | Failed promotion preserves incumbents. Successful admission is not reversed by incumbent cleanup errors. Recovery and claim loss retain their existing semantics. |

Screen outcomes, message closure reasons, session membership, and persistence claims remain
distinct concepts. Similar names or overlapping lifetimes are not evidence that they can be merged.

## Implementation sequence

### 1. Establish the actual state and call paths

Trace these operations through their callers and tests:

1. Open a sessionless screen and a screen with a session policy.
2. Handle a button press and a modal submission, including unchanged rendering and failed delivery.
3. Complete a child screen, then close its parent while another child is still opening.
4. Replace a durable session, including failure before registration and during incumbent cleanup.

For each path, record the state owner, lock, commit point, and cleanup owner at each await.
Keep this as a compact table in this plan. Inventory flags and owning collections only in
these paths; identify derived values, duplicated mutations, and genuinely independent states.
Record which modules mutate each retained field before and after the refactor.

Read the existing lifecycle tests first. Add behavior tests only for uncovered boundaries;
do not encode the current private call sequence. This phase must produce specific deletion
targets and a baseline test result before structural changes begin.

### 2. Make candidate publication an explicit boundary

Use `_CandidateLifecycle` as the owner of candidate settlement and published view state.
Audit writes from `MessageRoot` into its fields and replace scattered multi-field mutations
with a small number of meaningful transitions. Keep rendering and Discord delivery in the
root unless moving them also removes shared mutable state.

Preserve the differences between application candidates, undrawn plans used for suppression,
and renewal or terminal views. Consolidate representations only where the phase-one inventory
shows equivalent state. Check whether synchronous test staging requires production-only
bookkeeping that can instead live in the testing helper, after checking all callers.

Acceptance: each publication or discard has one implementation; callers do not reconstruct
its state updates. Existing suppression, renewal, cancellation, and failed-edit tests pass.

### 3. Consolidate interaction execution

Give button and modal execution one implementation of the acknowledgement watchdog and task
lifetime. Then consolidate their shared action-result and presentation handling where the
ordering is identical. Keep validation, event construction, busy painting, and challenge
presentation as explicit differences.

Use private functions or a small internal context only when they reduce argument threading
or duplicated state. Avoid a configurable pipeline or a context object that merely forwards
the whole root. Preserve action and render lock boundaries; do not hold either lock across
challenge user input or move acknowledgement behind rendering.

Acceptance: acknowledgement timing and cancellation have one shared implementation, with
tests for both buttons and submissions. Failure reporting, busy restoration, form errors,
and stale-interaction behavior remain unchanged.

### 4. Put shutdown bookkeeping behind its owner

Keep `Shutdown` responsible for one accepted cleanup operation and its shared result.
Give root dependency registration and removal one internal owner, so screen and session code
no longer edit root shutdown collections directly. Release dependencies after cleanup settles,
including failures, and preserve self-wait detection throughout the attachment tree.

Use the phase-one inventory to remove duplicated lifecycle flags only where one authoritative
state already determines them. Preserve the distinction between close acceptance and completed
cleanup, and between a pending screen result and an accepted outcome. Do not create a generic
lifecycle framework or force all these objects through one enum.

Acceptance: closing a scope, a parent screen, or a session follows the same dependency
registration rules. Cancellation, child collectibility, callback failures, and concurrent
waiters retain their existing guarantees.

### 5. Simplify opening and admission bookkeeping

Review `Scope._present`, `Scope._deliver`, `SessionManager._open_locked`, and durable
registration together. Name the points at which ownership transfers and keep failed-open
cleanup with the owner responsible at that point. Remove duplicate rollback or registration
bookkeeping identified in phase one; keep the sessionless path straightforward.

Preserve session visibility during initial render, quota checks under the member lock,
durable work before registration, and retirement after successful admission. Keep screen
parentage and session attachments separate unless their invariants and lifetimes are proven
identical. Remove obsolete internal parameters such as the unused session argument to
`_screen_bind` once callers have been checked.

Acceptance: ordinary, durable, and recovered sessions pass the same applicable admission
and attachment behavior tests. No compatibility adapter or duplicate opening path remains
solely to support this refactor.

## Validation and delivery

Implement the phases in order, using small, independently valid commits. Split a phase into
multiple commits when necessary, but include removal of superseded code with its replacement.
If a proposed extraction merely relocates methods or increases coordination, revise or drop it.

Run focused tests with `uv run --locked pytest --no-cov` during each phase and after its final
edit. Select the affected files from these groups:

- Publication: `test_candidate_lifecycle.py`, `test_render_pipeline.py`, `test_render_cache.py`,
  `test_missed_redraws.py`, and classic/V2 root tests.
- Dispatch: `test_action_finish.py`, `test_form_discord.py`, `test_challenges.py`,
  `test_error_policy.py`, and relevant dispatch tests in `test_message_root.py`.
- Ownership: `test_shutdown.py`, `test_ownership.py`, `test_direct_children.py`,
  `test_screen_flows.py`, `test_screen_delivery.py`, and `test_sessions.py`.
- Admission: `test_facade_scope.py`, `test_facade_response.py`, `test_adoption.py`,
  `test_durable_runtime.py`, and `test_target_recovery.py`.

Run the whole Discord package test suite after the final code change because these paths
interact. Run affected bot integration and architecture tests, including public API and
package-boundary checks. Format and lint changed Python files, run `git diff --check`, and
run `just typecheck` for the whole workspace. Let CI run the full repository suite unless
cross-package impact or unavailable CI warrants a local run. Compare existing render-cache
benchmarks if rendering or suppression work changes.

The final review should show fewer independently mutated lifecycle fields, fewer modules
writing another owner's private state, and less duplicated dispatch/cleanup logic. Include
before/after evidence from phase one. A smaller `message_root.py` with the same coordination
spread across more files does not satisfy this plan.

## Ownership audit

The initial focused baseline passed all 146 tests covering candidate settlement, action
completion, forms, shutdown, screen flows, and durable sessions.

| Path and awaited work | State owner and serialization | Commit and cleanup |
| --- | --- | --- |
| Scope presentation, initial loads, destination send | Scope claims the screen; root render lock protects staged candidates. Session admission additionally holds the key lock. | Delivery commits the candidate. Screen opening failure closes the bound root; candidate attempt discards unsettled renders. |
| Session collision selection, send, member lock, durable promotion and store commit | Session manager owns indexes; member lock protects the final quota check and registration. Durable runtime owns reservation and claim. | Register only after durable preparation. Promotion failure closes the newcomer; unconsumed reservation is abandoned. Retirement errors do not undo registration. |
| Button or form handler and subsequent presentation | Action lock follows binding mode; transaction owns handler writes. Render lock protects later edits. Watchdog runs independently. | Handler commit precedes presentation. Candidate discard preserves live bindings; failed application edits mark the message behind. |
| Child completion and parent callback | Screen owns pending outcome; parent dispatch supplies fresh authority and its own transaction. | Parent commit accepts the result. Failed callback leaves the child retryable; accepted results close the child even if later rendering fails. |
| Parent close during child opening | Root render lock serializes close acceptance; screen parentage exists before delivery. Session attachment validates parent after awaited delivery. | Accepted shutdown belongs to the execution host. Bound child roots remain wait dependencies until cleanup settles. |
| Child cleanup, final edit, services, finish callbacks | Root owns shutdown; screen and session supply child cleanup. | First accepted options are final. Each cleanup stage is attempted, and every waiter observes the stored result. |

Concrete deletion targets:

- Root `_finished` duplicates `Shutdown.accepted`: both change synchronously during close
  acceptance with no intervening await. Derive `finished` from the shutdown owner.
- Screen and session modules both append/remove root shutdown dependencies and compose
  child cleanup. Root methods should own registration, ordering, and error collection.
- Button and modal execution duplicate watchdog task lifetime and successful presentation
  handling. Share those operations while retaining their different error and validation scopes.
- `SessionManager.open` duplicates the complete call to `_open_locked` solely to omit a
  key lock for an unkeyed session. Use an optional context manager.
- `Screen._screen_bind` accepts an unused session argument. Session lookup already uses
  the manager's index, including during initial render.

Candidate publication is already substantially centralized. The remaining root write is
the failed-edit `behind` flag; publication repeats generation/view/timeout installation for
application and renewal candidates. Consolidate these transitions without changing planning.
Retain staged-candidate ownership: `_stage` is used by terminal rendering as well as tests,
so moving its bookkeeping into testing helpers would split a real lifecycle invariant.

Retain screen `claimed`, `opened`, `closing`, and `pending`: they distinguish failed opening,
callback eligibility, finalization, and an outcome awaiting action commit. Retain session
graph, retiring graph, and scope registry because they represent membership, cleanup in
progress, and application ownership respectively.
