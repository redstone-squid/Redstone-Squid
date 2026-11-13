# Test suite audit

First pass: 2026-11-13. This is an area-based review of test cost and coverage,
not an exhaustive certification that every remaining test is useful.

## Review method

Audit by area, then assign bounded edits to smaller-model agents. The reviewing
agent checks each proposed deletion against production behavior and retained
coverage before applying it. Keep changes in separate commits and run focused
tests with `--no-cov` before and after; run the project typecheck after the batch.

Prioritize these recurring classes:

- Oversized fixtures that exercise an expensive algorithm incidentally.
- Repeated planning, rendering, or setup for assertions about the same result.
- Tests that assert values supplied by their own fixtures without exercising behavior.
- Fixed sleeps used to establish ordering instead of explicit synchronization.
- Expensive shared infrastructure rebuilt for every test.

Do not use assertion count as a performance proxy. Keep type-narrowing assertions,
anti-vacuity checks, independent backend cases, malformed-input properties, and
subprocesses that require clean import state. A cheap checkpoint such as `sleep(0)`
is not itself evidence of waste. Do not reduce property-test budgets without
showing what coverage remains.

## Areas reviewed

| Area | Finding and disposition |
| --- | --- |
| Discord UI | Reduced a 2,000-line navigation fixture to 20 long lines while retaining budget-driven pagination. Consolidated duplicate planning in measurement/structure tests. Package suite: 1,893 tests / 49.43 s before, 1,890 / 25.81 s after, without coverage. Single local runs, not controlled benchmarks. |
| UI widgets | Conformance tests repeatedly render identical initial states. Combined shell checks and routed transition checks while retaining nonempty output, unique keys, route counts, result types, and deterministic transitions. Keep initial-state and arbitrary-action property checks. |
| Application unit tests | Replaced short scheduling sleeps in core concurrency tests with deterministic synchronization. Removed the voting matrix that reasserts fixture discriminators and repeats existing owner-guild mapping coverage. |
| Storage, replication, reactivity | Reviewed lifecycle and backend coverage; no deletion justified. Keep memory/SQLite cases, native-engine delivery-order permutations, and scheduler checkpoints. |
| Architecture and deployment | Removed duplicate catalog validation, overlapping import scans, and retired implementation-name guards after individual review. Keep fresh-process import boundaries, documented naming policies, and shared module-scoped Docker builds. |
| Database integration | 178 test functions in 42 files directly request `migrated_session_factory`, which creates a database and runs all migrations per invocation. Investigate separately with PostgreSQL timings and isolation validation. |

## Follow-up queue

1. Run thread-using tests outside the restrictive sandbox in this environment.
   Both the API and S3 tests stalled inside it; the focused group passed all nine
   tests in 0.41 s outside it. This is an execution-environment issue, not grounds
   to remove the tests. A fresh full-suite run is used for final validation.
2. Measure database setup separately from test bodies. A migrated template may
   reduce setup cost, but must preserve per-test isolation, triggers, constraints,
   and real commits. Do not replace migrations with `create_all` merely for speed.
3. Inspect heartbeat waits in `tests/unit/media/test_jobs.py` and integration
   expiry/notification waits. Replace them only with synchronization that still
   proves cancellation, TTL extension, and rollback suppression.
4. Continue application-domain reviews using measured cost and concrete overlap.
   Repeated fake construction alone is not grounds for a new fixture abstraction.

The first focused baseline for concurrency, widget conformance, and voting was
129 passing tests in 1.68 s. These are coverage/readability cleanups; the dominant
measured speedup so far comes from the Discord pagination fixture.

Batch validation: 153 focused tests passed, whole-project Pyrefly reported zero
errors, and installed Ruff commit hooks passed. The focused run uses
`HYPOTHESIS_STORAGE_DIRECTORY=/tmp/squid-audit-hypothesis` because the repository
cache directory is not writable in this session.

## Second application pass

Three smaller-model agents reviewed bot tests, API tests, and application domain
coverage. Parent review accepted these changes:

- Accounts: replace 2,000 random verification-code draws and a probabilistic
  collision assertion with deterministic lower/upper boundary cases. Check the
  requested RNG range and exact outputs.
- Builds: remove 18 parser input/output pairs already exercised by the shared
  duration fixture. Keep Python `None` and four additional grammar/error cases.
- Bot: remove the command collision inventory scan. The retained all-extensions
  loading test exercises discord.py registration, which rejects duplicate names
  and aliases before the inventory could be inspected.
- API: create the OpenAPI-only app without unused fake database/service setup.
  Its lifespan is never entered; HTTP behavior tests retain their runtime fakes.

The second batch passed 62 focused tests and whole-project Pyrefly. Review kept
the help entrypoint/query matrix, localized permission-node hashability check,
and request-ID helper/wire checks: each protects behavior that a superficial
similarity or self-comparison heuristic would miss.

## Broad validation and remaining costs

The default suite completed outside the sandbox: 6,852 passed, one skipped, and
one failed in 125.61 s, without coverage. The failure reproduced in isolation:
`test_service_graphs` incorrectly required the optional `schematic_summaries=None`
field to be an unconfigured stand-in. The assertion now checks its explicit
absence while retaining the checks on every other service. Its focused rerun
passed all eight tests. The full suite was not rerun after this isolated test fix.
Integration and Docker suites were reviewed but not executed in this pass.

The slowest full-run tests were the Discord render-cache comparison (8.90 s) and
change-scenario benchmark (5.29 s), followed by two catalog validation checks
(2.68 s and 2.48 s). The benchmark probes already request two samples; investigate
setup and garbage-collection costs under a fully loaded test process before
reducing their meaningful cache-path assertions. These timings are not directly
comparable with the earlier isolated Discord package runs.

## Individual necessity review

The follow-up reviews test bodies against production behavior and retained
coverage. The [inventory](test-suite-audit-inventory.csv) records each file's
status and decisions. Earlier area sampling does not count as individual review.
The current checkpoint covers 542 of 636 tracked test files and 5,095 reviewed
test declarations, including embedded Rust tests. Parameterized cases are not
counted separately. Newly added tests remain pending until reviewed.

Accepted changes include:

- Remove duplicate explicit logging-path coverage, constructor-algorithm echoes
  in account normalization, and an internal legacy-name absence guard. Preserve
  persisted legacy-key handling and database table compatibility.
- Share configuration loads across ten rejected keys, merge observability
  defaults, and remove duplicate submission-capacity projection coverage.
  Keep unknown-key rejection from both environment and dotenv sources.
- Replace web truthiness and substring checks with exact output or parsed JSON
  contracts. Remove a message-regex matrix that also accepted undefined labels.
- Make the Minecraft optional-control test actually include an unsupported
  control, check the exact Paper installation header, and remove gzip checks
  already proved by a complete compressed-byte golden.
- Make Rust profile assertions unconditional and replace the replication undo
  test's self-referential tree expectation with an explicit empty forest.

Focused validation passed: configuration/runtime/service-graph tests (92 cases),
record vocabulary/permission administration (20), Loro adapter (16), web units
(152), the changed Chromium detail scenario, Rust CLI library (10), and the three
affected Minecraft modules using JDK 25. These later changes improve test value;
no additional suite-wide speedup has been measured. Extra boundary cases suggested
by the Rust and Minecraft reviews are recorded as follow-ups, rather than added
as part of this cleanup. Browser projects other than the focused Chromium case
were not rerun.

## Individual review checkpoint: application and Discord

Completed body reviews now include application unit areas, UI core/state/storage,
web/API/bot, schematic/media workers, and the first integration batches. Remaining
files are explicitly pending in the inventory; reviewed files with newly added
test names are partial. A file marked reviewed may retain a proposed cleanup
pending parent review. This is a shared-worktree snapshot, not a guarantee that
subsequent edits to an existing test body have been re-reviewed.

Further accepted changes:

- Remove duplicated catalog execution and architecture scans already covered by
  broader retained checks; remove ineffective importlib module-shape assertions.
- Remove unused build helper/constructor checks, fake-only suggestion filtering,
  repeated refresh-formatting smoke tests, and configuration/registry echoes.
- Preserve save/commit/index ordering, make the cache concurrency fake actually
  yield, and verify exact enum-to-post mappings and committed configuration edits.
- Repair vacuous Discord tests: use actual file assets, create the session whose
  detail is asserted, finish the root before checking its stopped view, and put
  fallback fields under real shared-budget pressure.
- Retain the vote attachment test as a commit contract: replacing the fake list
  makes a missing commit observable. Remove its repeated delegation call.
- Remove the unused database sanity-check implementation and its entire test
  module at the user's request. It had no callers outside its own tests.

Recent focused checks passed: build/submission (355), bot/UI/i18n/widgets (152),
notification/starboard/reaction/event/message/settings/sync/tag tests (113),
suggestion/sync (84), and community/diagnostic/vote services (73). The repaired
Discord files passed their focused runs, including real shared-budget overflow.
The enum-name assertion removal in database integration was reviewed but that
integration suite was not executed.

The architecture/catalog check still reports stale generated English catalogs
from concurrent UI changes; it failed before removal of the duplicate check too.
The latest whole-project typecheck reports one error in the concurrently edited
widget browsing test: a fixed two-element tuple inferred for `source.items` is
later assigned one element. Previous typechecks passed; no suite-wide timing
claim is made for these later cleanups.
