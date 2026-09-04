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

As of `1689c770` (2026-08-30), the numbered plans have been implemented and independently
re-audited against current code. Each plan's findings and dispositions remain the historical
reasoning; this table records the resulting implementation state. **Done** means every in-scope
implementation and test case is present. **Blocked** is reserved for verification that requires
external CI infrastructure rather than more repository work.

The review inventories below the numbered plans are scoping records, not implementation plans.
GitHub replies and thread resolution also remain separately authorized work.

| # | Plan | Status | Notes |
|---|---|---|---|
| 1 | [Consent and verification UX](01-consent-verification-ux.md) | Done | Reservation/error contracts remain intact. `61eb77e0` adds the complete consent-card payload/interaction branches, claim approve/reject/conflict presentation, and the missing resolution query-count scenario. |
| 2 | [User identity and persistence](02-user-identity-persistence.md) | Done | Provider-neutral identity, keyed verification-code hashing, and schema constraints remain intact. `61eb77e0` also makes service-unavailable identity failures a stable bot presentation and pins that path. |
| 3 | [Submission UI architecture](03-submission-ui-architecture.md) | Done | `2a17f95d`, `3e278df6`, and `36f5e9d9` make creation/edit specs the single typed source for metadata, parsing, formatting, and draft/patch application; shared input parsing, colour typing, translated edit metadata, strict Discord boundaries, and unexpected-parser propagation are covered. |
| 4 | [Submission behavior and recovery](04-submission-behavior-recovery.md) | Done | Detailed URL errors, exhaustive neutral status rendering, revision fencing, truthful saved-vs-delivery outcomes, durable timeout controls, and current-state fresh-editor recovery landed across `40db03e7` through `2fcba94b`, with authorization and stale-state tests. |
| 5 | [Multi-attachment semantics](05-multi-attachment-semantics.md) | Done | Identity-based lifecycles, explicit primary selection, partial failure evidence, all-file duplicate merging, same-digest coalescing, compact titled review evidence, and post-save recovery landed in `d5c14d7a` through `7f63c81b`. |
| 6 | [Schematic domain upload safety](06-schematic-domain-upload-safety.md) | Done | `e1ed933a` adds `RgbaColor`, the resource-pack value contract, request documentation, and wire/config reuse. `da36c94c` pins cache invalidation and provider-neutral uploader attribution; `5bba214a` keeps the value failures structured. The bounded pre-parser remains deliberately retained and adversarially covered. |
| 7 | [Nucleation adapter, wire, and worker hardening](07-nucleation-adapter-worker-hardening.md) | Done | `4bd049e9`, `a31e8aa7`, `6ec2b579`, and `b57c1445` remove the format fallback, strictly decode native/wire data, contain optional evidence failures, and validate all operation arities. `130c373e` and `6ce20b89` apply one cumulative deadline through queueing, cold start, write, and read, then bound failure cleanup separately and defer restart backoff. The current `0.10.14` exception mismatch is reported as [Nucleation #40](https://github.com/Schem-at/Nucleation/issues/40) and cited beside the narrow workaround. |
| 8 | [Schematic rendering and simulation](08-schematic-rendering-simulation.md) | Done | Typed render outcomes and durable retry behavior remain intact. `47b53ebb` adds permanent-skip/retry/backoff/dead-letter PostgreSQL scenarios and replaces bare integration-test tasks with structured anyio ownership; real worker-pool tests pass locally. |
| 9 | [Voting redesign](09-voting-redesign.md) | Done | Domain and persistence subtype invariants, serialized rollout, complete rejection/alias mappings, application/REST discriminator matrices, reaction recovery, and upgrade/downgrade scenarios landed through `bc8874e6`. PostgreSQL execution is pending external CI, but the implementation and tests are present. |
| 10 | [Shared reaction routing](10-shared-reaction-routing.md) | Done | Typed subscriptions, supervisor-owned anyio workers, FIFO/backpressure accounting, resolver memoization, failure/latency/shutdown telemetry, and consumer-owned recovery landed through `9d460311`. `4b1c6971`, `8aee6bd7`, `07ad1375`, and `1689c770` add bounded accepted-event handoff, serialized explicit-state vote recovery, stable cross-guild alias handling, periodic repair, and operator-actionable deferred-intent logs for timeout and callback failure. |
| 11 | [API, auth, records, and sync](11-api-auth-records-sync.md) | Done | Existing caller/error/sync decisions remain intact. `b1032ee7` adds canonical persisted-scope-array coverage and substantive multi-item schematic pagination/anchor coverage. |
| 12 | [Runtime and observability](12-runtime-observability.md) | Done | The authoritative log transport, correlation display, resolved telemetry record, explicit worker trace field, typed public command surfaces, FastAPI error registration, join/message correlation, and three-process lifecycle contract all landed from `ae9edae0` through `89d9dde0`. |
| 13 | [Test and tooling cleanup](13-test-tooling-cleanup.md) / [dispositions](13-test-tooling-dispositions.md) | Blocked | All repository work and thread dispositions are complete, including the typed reaction-callback update in `df1302d1`. The remaining condition is the already-documented green GitHub CI/PostgreSQL verification; local Docker access is unavailable. |

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
