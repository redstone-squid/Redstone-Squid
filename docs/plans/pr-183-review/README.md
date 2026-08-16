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

## Status

As of `77dee05c` (2026-08-16). Each plan's own Findings section already audits current HEAD, so this
table is a summary, not a replacement — read the plan for the item-level detail. Statuses:
**Done** (all in-scope items landed), **In progress** (some items landed, some open),
**Not started** (open per the plan's own findings, unchanged since), **Blocked** (needs external
input, e.g. CI, to close).

| # | Plan | Status | Notes |
|---|---|---|---|
| 1 | [Consent and verification UX](01-consent-verification-ux.md) | Not started | Link prompt is still dense prose, staff claim views still show only the internal account ID, and `AliasAlreadyClaimedError` still omits conflict context — no commits since the audit touch `squid/bot/consent.py` or alias errors. |
| 2 | [User identity and persistence](02-user-identity-persistence.md) | In progress | The provider-neutral `accounts` context and single `normalize_ign` were already in place at the audit. The rename-lifecycle repository operation, normalization-equivalence tests, and mapping-by-aggregate audit remain open. Subplan 6 (verification-code digest, entropy, and attempt caps) was added from plan 11's crypto audit and has not started. |
| 3 | [Submission UI architecture](03-submission-ui-architecture.md) | Not started | `EDIT_FIELDS` centralization already landed pre-audit; the typed field-spec/factory redesign, submission-input module, and presentation-colour value type have not started. |
| 4 | [Submission behavior and recovery](04-submission-behavior-recovery.md) | In progress | Invalid-URL rejection and search/confirmation copy were already fixed pre-audit. Build-card status still renders unknown/`None` as confirmed green, and expiry sessions still have no recovery action. |
| 5 | [Multi-attachment semantics](05-multi-attachment-semantics.md) | Not started | Attachment classification/error text was already fixed pre-audit. Primary-schematic selection is still implicit "first successfully analysed", and duplicate lookup still only examines `analyses[0]`. |
| 6 | [Schematic domain upload safety](06-schematic-domain-upload-safety.md) | Not started | Verified in code: `IngestRequest.uploaded_by_discord_id` (`squid/schematics/application/commands.py:15`) and `RenderRequest.background: tuple[float, float, float, float]` (`squid/schematics/application/commands.py:42`) are both still present, matching the plan's findings exactly. |
| 7 | [Nucleation adapter, wire, and worker hardening](07-nucleation-adapter-worker-hardening.md) | In progress | Worker timeout/queue-wait accounting was already fixed pre-audit. The silent `or SchematicFormat.LITEMATIC` fallback is still live at `squid/schematics/infrastructure/nucleation_adapter.py:379`; typed JSON decoding and the upstream 0.10.1 reproduction are still open. |
| 8 | [Schematic rendering and simulation](08-schematic-rendering-simulation.md) | Done | All six steps landed in `74c17dea`…`47e6f053`; see the plan's implementation notes. `prepare_render()` now answers `FreshRender \| CachedRender \| SkippedRender`, the ambiguous-input refusal lists the coordinates it will accept (and a named coordinate now wins over the annotation, which it did not before), and the stderr pumps are owned by the pool's anyio task group. `SimulationResult` is retained with a recorded rationale. |
| 9 | [Voting redesign](09-voting-redesign.md) | Mostly done | `509406c2` ("voting: type the domain and decouple polls from Discord", 2026-08-16 00:16) landed after this plan was written: `StrEnum`s, `BuildVoteTarget`/`DeleteLogVoteTarget`, nullable thresholds with a check constraint, guild-independent poll creation, the `/poll` select-menu wizard, a `PollPublisher` facade, and reaction restoration on clear. `AbstractVoteSession`/`base_session.py` is gone and `close_due` now runs under `BackgroundTaskSupervisor` (`squid/worker/app.py:124`). Remaining: full test-matrix parameterization (subplan 5) is unverified. |
| 10 | [Shared reaction routing](10-shared-reaction-routing.md) | Not started | Verified in code: `squid/bot/reactions.py:188` still spawns shard workers with bare `asyncio.create_task`, exactly as the plan's finding describes. No task-ownership, observability, or contract-narrowing work has landed. |
| 11 | [API, auth, records, and sync](11-api-auth-records-sync.md) | Done | All eight subplans landed in `9311019d`…`5ac64ea4`. Three things were found while implementing and are recorded in the plan: the contract export was interpreter-dependent (fixed separately in `0a492f8c`, which also revealed a forgotten regeneration); `ReconciliationResource` needs one seam with `squid.posts.domain`, because unifying the two spellings reaches the excluded starboard paths; and three uses of "principal" survive as external contracts (the `idempotency_requests` column, the `RateLimit-Policy` partition, and `SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`). Its verification-code finding remains with plan 2 §6. |
| 12 | [Runtime and observability](12-runtime-observability.md) | In progress | New since the audit: `bf07c23d` binds a request-scoped correlation id, `686a55c0`/`874e576d` fold `X-Error-ID` into a single `Request-Id` response header, and `1ff42c54`…`77dee05c` unprefix the remaining `X-`-style headers. Subplan 2 (the short display reference) has landed: `correlation_reference()` shortens to 12 characters, `ErrorPresentation` carries `reference` beside `error_id`, and both widths are logged. It arrived with error-report storage, which needed a stable per-invocation correlation ID and so also bound one around every Discord command. Log-transport documentation and the welcome-relay sleep are unverified. |
| 13 | [Test and tooling cleanup](13-test-tooling-cleanup.md) / [dispositions](13-test-tooling-dispositions.md) | Blocked on CI | Now also owns the previously unassigned `alembic_entities.py` counts thread (3782845586), which is planned but not started. Every other thread has a recorded disposition (mostly Fixed/Already fixed, one Retained-with-rationale each for taxonomy and migration-downgrade tests, one Deferred to plan 12). The two integration-test changes (`test_vote_repository.py`, `test_alembic_migrations.py`) need a CI run against Postgres before their threads can close, per the plan's own verification-status note. |

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
