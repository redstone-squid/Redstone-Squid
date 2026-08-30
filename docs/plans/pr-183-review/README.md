# PR #183 review follow-up plans

## Scope

This directory organizes the review comments made by `Glinte` on
[PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183), using each inline comment's
original commit anchor to enforce the cutoff at `5edfd3e`.

- 184 comments are in scope across 86 files.
- All 184 threads remain open on GitHub; 33 are marked outdated and 151 still have current anchors.
- Starboard paths and behavior are excluded. Shared reaction routing remains in scope because it
  serves non-starboard consumers.
- The later comment on the legacy `/verify` endpoint is excluded because its original commit is
  after `5edfd3e`.
- The cluster split left one thread unassigned — 3782845586 on
  `squid/persistence/alembic_entities.py`, which is migration-authoring tooling rather than tests,
  identity, or API. It is now dispositioned in plan 13.
- Each plan audits current HEAD so later fixes are credited rather than accidentally reverted.

The broad review clusters were: submission UX (42 comments), voting (34), schematics (33),
API/auth/sync (25), runtime/observability (18), identity/permissions (14), tests/tooling (10), and
shared reaction routing (8).

## Plans

1. [Consent and verification UX](01-consent-verification-ux.md)
2. [User identity and persistence](02-user-identity-persistence.md)
3. [Submission UI architecture](03-submission-ui-architecture.md)
4. [Submission behavior and recovery](04-submission-behavior-recovery.md)
5. [Multi-attachment semantics](05-multi-attachment-semantics.md)
6. [Schematic domain contracts and upload safety](06-schematic-domain-upload-safety.md)
7. [Nucleation adapter, wire, and worker hardening](07-nucleation-adapter-worker-hardening.md)
8. [Schematic rendering and simulation](08-schematic-rendering-simulation.md)
9. [Voting redesign](09-voting-redesign.md)
10. [Shared reaction routing](10-shared-reaction-routing.md)
11. [API, auth, records, and sync](11-api-auth-records-sync.md)
12. [Runtime and observability](12-runtime-observability.md)
13. [Test and tooling cleanup](13-test-tooling-cleanup.md) —
    [thread dispositions](13-test-tooling-dispositions.md)

## Review inventories

- [Comments from `2605367` through `aa85f68`](14-review-comment-inventory.md) records the later
  pending review batch: 104 comments grouped by primary concern. It excludes the CLI, web
  frontend, and Minecraft plugin by their `cli/`, `web/`, and `minecraft/` paths, while retaining
  comments on shared backend code under `squid/` regardless of commit-subject prefix.

## Status

As of `56369af4` (2026-08-18). Each plan's own Findings section already audits current HEAD, so this
table is a summary, not a replacement — read the plan for the item-level detail. Statuses:
**Done** (all in-scope items landed), **In progress** (some items landed, some open),
**Not started** (open per the plan's own findings, unchanged since), **Blocked** (needs external
input, e.g. CI, to close).

Note: a branch rebase during this refresh invalidated most commit hashes cited across these plans
(the underlying commits still exist under new hashes, matched by message/content, not lost). Plans
were re-verified against current file:line citations rather than the old hashes; a few citations
were left uncorrected where doing so risked colliding with concurrent edits — treat any hash in a
plan below as historical, not resolvable with `git show`.

| # | Plan | Status | Notes |
|---|---|---|---|
| 1 | [Consent and verification UX](01-consent-verification-ux.md) | Done | All five subplans re-verified against current code: the card-based consent view with a real preview, two-step verification-code reservation (`reserve_verification_code`/`release_verification_code`, `LinkReservationExpiredError`), shared link/refresh reconciliation rendering, claimant naming, and `AliasAlreadyClaimedError` carrying both the public and internal holder identity. Two `BUGS.md` entries it deferred remain open and untouched. |
| 2 | [User identity and persistence](02-user-identity-persistence.md) | Done | Subplans 1–5 confirmed already in place; subplan 6 re-verified: `generate_verification_code` mints a ten-digit (~33-bit) code in the application layer, `hash_verification_code` uses a keyed HMAC, and `verification_attempts` caps consecutive failures per `(provider, subject)`. Keyed on the external identity rather than an account because the guesser may not have one yet, which also lets plan 1's anonymous reservation share the guard. |
| 3 | [Submission UI architecture](03-submission-ui-architecture.md) | Not started | Re-verified unchanged: `EDIT_FIELDS` centralization is the only landed piece; the typed field-spec/factory redesign, submission-input module, and presentation-colour value type have not started. |
| 4 | [Submission behavior and recovery](04-submission-behavior-recovery.md) | In progress | Re-verified unchanged: invalid-URL rejection and search/confirmation copy remain fixed; the URL error still omits offending values, edit-session expiry still has no recovery action, and build-card status still falls back to green for an unknown/`None` state. |
| 5 | [Multi-attachment semantics](05-multi-attachment-semantics.md) | Not started | Re-verified unchanged: attachment classification/error text remains fixed; primary-schematic selection is still implicit "first successfully analysed", and duplicate lookup still only examines `analyses[0]`. |
| 6 | [Schematic domain upload safety](06-schematic-domain-upload-safety.md) | In progress | Updated since the last audit: `IngestRequest.uploaded_by_discord_id` is gone, replaced by a provider-neutral `uploaded_by_account_id: int \| None` (landed alongside the Discord de-privileging work). `RenderRequest.background`'s unlabelled RGBA tuple, the resource-pack value object, and `SimulationRequest` field documentation remain open. |
| 7 | [Nucleation adapter, wire, and worker hardening](07-nucleation-adapter-worker-hardening.md) | In progress | Worker timeout/queue-wait accounting was already fixed pre-audit. The silent `or SchematicFormat.LITEMATIC` fallback is still live; typed JSON decoding is still open. The plan's cited nucleation pin (`0.10.1`) is stale — the project now pins `0.10.14` — so the upstream #7/#8 reproduction needs to run against the current pin before this can close. |
| 8 | [Schematic rendering and simulation](08-schematic-rendering-simulation.md) | Done | All six steps re-verified in current code: `prepare_render()` answers `FreshRender \| CachedRender \| SkippedRender`, the ambiguous-input refusal lists the coordinates it will accept, and the stderr pumps are owned by the pool's anyio task group. `SimulationResult` is retained with a recorded rationale. |
| 9 | [Voting redesign](09-voting-redesign.md) | Mostly done | Subplans 1–4 (domain typing, transport-independent polls, the `/poll` UI, session cleanup) re-verified fully landed — the presentation layer was generalized further than planned, into a shared reconciler/renderer. Subplan 5 is genuinely partial: fixture/builder extraction landed (`tests/support/voting.py`, `tests/support/schema.py`), but the described cross-product test matrix across `VoteKind`/`VoteTarget`/`VoteVisibility`/`VoteChoice`/`VoteRejection` was never started — only scattered single-axis parametrization exists. |
| 10 | [Shared reaction routing](10-shared-reaction-routing.md) | Not started | Re-verified unchanged: `squid/bot/reactions.py` still spawns shard workers with bare `asyncio.create_task` and drives shutdown/subscriber dispatch off raw tasks and `asyncio.gather`, even though `BackgroundTaskSupervisor` is already used elsewhere in the bot. No task-ownership, observability, or contract-narrowing work has landed. |
| 11 | [API, auth, records, and sync](11-api-auth-records-sync.md) | Done | All eight subplans re-verified landed, and all three recorded caveats (pinned 422 reason phrase, `ReconciliationResource`'s hand-mapped `post_kind`, three surviving "principal" external contracts) remain accurate as documented. The later `contract()`/OPERATIONS-table migration work is unrelated follow-on scope, confirmed to belong to [`rest-api.md`](../completed/rest-api.md), not this plan. |
| 12 | [Runtime and observability](12-runtime-observability.md) | In progress | Subplan 2 (short correlation-id display reference) confirmed done, and it went on to enable error-report storage with a per-invocation correlation ID bound around every Discord command. Subplan 1 (log-transport documentation) is still not started, though an unrelated Docker fix incidentally settled the log-volume-ownership question by switching to a named volume. Subplans 3–8 (telemetry-guard collapse, worker trace-header field, command-span naming, the `squid/api/errors.py` FastAPI revert, the welcome-relay sleep/correlation fix, and the entry-point lifecycle test rewrite) show no code changes at all. |
| 13 | [Test and tooling cleanup](13-test-tooling-cleanup.md) / [dispositions](13-test-tooling-dispositions.md) | In progress | The previously unassigned `alembic_entities.py` counts thread (3782845586) is now **Fixed**: `parse_entities(sql)` takes the SQL as an argument, an `EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS` guard replaces the two hand-maintained magic numbers, and `tests/unit/persistence/test_alembic_entities.py` covers it; the doc-remnant bump instructions in `rbac.md`/`durable-queues.md` and the stale path in `new-migration.md` are also now fixed. Every other thread has a recorded disposition (mostly Fixed/Already fixed, one Retained-with-rationale each for taxonomy and migration-downgrade tests, one Deferred to plan 12). The two integration-test changes (`test_vote_repository.py`, `test_alembic_migrations.py`) still need a CI run against Postgres before their threads can close. |

## Suggested sequence

1. Settle provider-neutral identity and shared value types in plans 2, 6, and 11.
2. Implement the submission contracts and UX in plans 1 and 3–5.
3. Harden the schematic boundary before changing orchestration in plans 7 and 8.
4. Redesign voting contracts before simplifying reaction routing in plans 9 and 10.
5. Handle runtime/observability decisions in plan 12, then perform the test cleanup in plan 13
   alongside or immediately after the affected implementation plan.

Treat each numbered plan as a separate planning and implementation unit. Before closing GitHub
threads, produce a thread-level checklist that records one of four dispositions: fixed by the new
work, already fixed after `5edfd3e`, retained with rationale, or deferred to a named follow-up.
GitHub replies and thread resolution require separate explicit authorization.

## Delivery convention

- Commit each coherent implementation milestone independently using component-scoped imperative
  subjects.
- Run the smallest focused tests while developing, then changed-file formatting/type checks,
  `git diff --check`, and `alembic heads` when persistence is touched.
- Recheck Nucleation documentation and reproduce behavior on a clean pinned install before filing
  or retaining an upstream workaround.
