# PR #183 review follow-up plans

## Scope

This directory organizes the review comments made by `Glinte` on
[PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183), using each inline comment's
original commit anchor to enforce the cutoff at `5edfd3e`.

- 184 comments are in scope, across 85 paths as GitHub names them today (86 at the last refresh;
  a thread is reported against a file's present name, so a rename can merge two).
- All 184 threads remain open on GitHub. Outdatedness has moved sharply as the branch advanced:
  175 are now marked outdated and only 9 still have current anchors, against 33/151 at the last
  refresh. Only three threads on the whole PR are resolved and all three are CodeQL bot findings,
  not review comments.
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
- **Uncovered:** 52 further `Glinte` threads anchor to commits after `aa85f68` and so fall outside
  both the `5edfd3e` plan cutoff and that inventory's range. They cluster on `squid/accounts/`
  (repository, services, models, ports), `squid/bot/verify.py`, `squid/bot/voting/vote.py`,
  `squid/diagnostics/log_capture.py`, and `squid/api/v1/schemas/me.py`. Nothing here plans them;
  inventorying them is the next scoping step.

## Status

As of `e6564821` (2026-08-30), re-audited against current code. Each plan's own Findings section
carries the item-level detail; this table is a summary, not a replacement. Statuses:
**Done** (all in-scope items landed), **In progress** (some items landed, some open),
**Not started** (open per the plan's own findings, unchanged since), **Blocked** (needs external
input, e.g. CI, to close).

Note on commit hashes: the rebase warning carried by the previous refresh is retired. 56 of the 60
hashes cited across these plans resolve with `git show` today. The exceptions are this table's old
anchor `56369af4`, plan 01's audit anchor `f4cd124b`, and two GitHub thread IDs (`3775316974`,
`3775329634`) that only look like hashes.

| # | Plan | Status | Notes |
|---|---|---|---|
| 1 | [Consent and verification UX](01-consent-verification-ux.md) | Done | Re-verified, no regression: `reserve_verification_code`/`release_verification_code` on the accounts port, `LinkReservationExpiredError`, and `AliasAlreadyClaimedError.with_holder_name` are all still in place. Two `BUGS.md` entries it deferred remain open and untouched. |
| 2 | [User identity and persistence](02-user-identity-persistence.md) | Done | Re-verified, no regression: `generate_verification_code` still mints in the application layer, `hash_verification_code` is still keyed HMAC (and now also gates merge-ticket lookup), and the `verification_attempts` table keeps its provider and non-negative-failure constraints. |
| 3 | [Submission UI architecture](03-submission-ui-architecture.md) | In progress | **Changed since the last refresh — was "Not started."** `5b8df7e3` landed `squid/bot/submission/ui/fields.py`: `BuildFieldSpec` carries patch key, label, parser, formatter, placeholder, requiredness, min/max, display mode, and category applicability, with a `.typed()` factory over the shared parser registry. That closes the typed-spec and factory items. `BoundBuildField.stage()` parses without touching the build and `_apply` assembles a typed `BuildEditPatch.from_attributes(...)`, closing the string-attribute-mutation item. The screen rewrite onto `sd.Screen`/`sl.forms.FormSpec` removed every `hasattr` probe from `ui/` and took the `DirectonalityLocationalitySelect` misspelling with it. Still open: comma splitting is a view-local `_split_values` (`ui/views.py:45`) rather than a submission-input module, and presentation colour is three module-level ints in `squid/bot/ui.py:21-23`, centralized but not a value type. Tests cover the spec contract (`tests/unit/bot/submission/test_edit_fields.py`, 3 cases) but not parser/formatter round trips or Discord boundary limits. |
| 4 | [Submission behavior and recovery](04-submission-behavior-recovery.md) | In progress | Two items unchanged: `_details_submitted` computes `invalid_urls` and then discards it for a generic "Every link must start with `https://` or `http://`" (`ui/views.py:350-355`), and the build card still ends `status_colours.get(build.submission_status, DISCORD_GREEN)` (`build_handler.py:190`), so an unknown or `None` status still renders confirmed. The expiry item moved sideways rather than being worked: the "reopen the build" copy is gone, `BuildEditScreen` is a 900s `sd.SessionSpec` whose resource reloads the build by id, and the framework default `PauseUpdates` offers "press any control to resume" instead of a dead panel. The described recovery action — a fresh session that warns about discarded edits — was not built. |
| 5 | [Multi-attachment semantics](05-multi-attachment-semantics.md) | Not started | Re-verified unchanged. Primary selection is still positional (`submit.py:231` passes `primary=index == 0`, docstring: "The first upload is primary"), and duplicate lookup, dimension prefill, and mismatch evidence all still read `analyses[0]` (`submit.py:173,252,257,280`). |
| 6 | [Schematic domain upload safety](06-schematic-domain-upload-safety.md) | In progress | Unchanged since the last refresh. `RenderRequest.background` is still a bare `tuple[float, float, float, float]` validated inline (`commands.py:54,65`) with no `RgbaColor` anywhere in the tree; `SchematicResourcePackProvider.load()` still answers an anonymous `tuple[bytes, str]`; `SimulationRequest.watch_positions`/`max_ticks` still carry no field documentation. The uploader-coupling fix (`3afb5315`) holds. |
| 7 | [Nucleation adapter, wire, and worker hardening](07-nucleation-adapter-worker-hardening.md) | In progress | Unchanged. `resolved_format = source_format or sniff_schematic_format(data) or SchematicFormat.LITEMATIC` is still live at `nucleation_adapter.py:379`, `_optional()` still swallows every exception across nine call sites, and no typed JSON decoding helper has landed. The pin is still `nucleation==0.10.14`, so plan step 3's upstream reproduction against the current pin remains the gate. |
| 8 | [Schematic rendering and simulation](08-schematic-rendering-simulation.md) | Done | Re-verified, no regression: `prepare_render()` still answers `FreshRender \| CachedRender \| SkippedRender`, matched exhaustively by `squid/worker/rendering.py:59-67`, and `SimulationResult` is retained. |
| 9 | [Voting redesign](09-voting-redesign.md) | Mostly done | Unchanged in substance; the test tree moved under it. `tests/support/voting.py` and `tests/support/schema.py` survive, but `test_dynamic_voting.py` is now `tests/unit/voting/` (was `tests/integration/bot/`) and `test_vote_repository.py` is now `tests/integration/voting/infrastructure/`. Subplan 5's cross-product matrix over `VoteKind`/`VoteTarget`/`VoteVisibility`/`VoteChoice`/`VoteRejection` still does not exist: 8 single-axis `parametrize` uses in the dynamic-voting tests, 2 in the repository tests, 0 in `tests/unit/api/test_vote_writes.py`. |
| 10 | [Shared reaction routing](10-shared-reaction-routing.md) | Not started | Re-verified unchanged. `squid/bot/reactions.py:188` still spawns shard workers with bare `asyncio.create_task`, and shutdown still drives producers, queue joins, and worker teardown through three `asyncio.gather` calls (`:152-160`). No task-ownership, observability, or contract-narrowing work has landed. |
| 11 | [API, auth, records, and sync](11-api-auth-records-sync.md) | Done | Re-verified, no regression. The `Principal` → `Caller` rename holds — the only surviving uses of "principal" in `squid/` are the three documented external contracts (`idempotency_requests.principal` and its unique index, the `RateLimit-Policy` partition, `SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`). The pinned 422 phrase (`errors.py:116`) and `ReconciliationResource.post_kind`'s hand-written match (`squid/sync/application.py:31-37`) are both still there with their recorded rationale. |
| 12 | [Runtime and observability](12-runtime-observability.md) | In progress | Subplan 2 stays done; subplans 1 and 3–7 show no code change at all. `squid/observability.py` still repeats the `ModuleNotFoundError` dance at seven sites with no `_Telemetry` record, still exports `inject_trace_context`/`extracted_trace_span` with `worker.py:152` splicing W3C keys into the frame header, and still keeps `_trace_endpoint`; `_interaction_command_name` still walks `interaction.data` (`bot/errors.py:424,461`); `squid/api/errors.py` still carries `ExceptionRegistrar` and its local `correlation_id`; `welcome_relay.py:45` still sleeps 30 seconds in the listener and still calls `guild.get_member` only to build a mention. Subplan 8 saw partial movement: `20658d54` ("bot: type process lifecycle fixtures") replaced the mock-graph namespaces that drew the original complaint, but `tests/unit/bot/test_app_main.py` is still four bot-specific tests with no failure path, and the API entry point's lifecycle test predates this plan (`63b51a74`, 2026-08-05) rather than being the table-driven three-process contract asked for. |
| 13 | [Test and tooling cleanup](13-test-tooling-cleanup.md) / [dispositions](13-test-tooling-dispositions.md) | Blocked | Every thread has a recorded disposition and the `alembic_entities.py` counts fix holds. The two integration-test changes still need a green CI run against Postgres, and CI cannot currently supply one: every `Continuous Integration` run on this branch and on `master` for the past several days ends in `startup_failure` or `action_required`, with no successful run to read. Unblocking CI is the prerequisite, not more test work. |

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
