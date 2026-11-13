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
| Architecture and deployment | No cleanup justified. Source parsing is already cached; import subprocesses need fresh interpreter state; Docker builds are module-scoped and shared across checks. |
| Database integration | 178 test functions in 42 files directly request `migrated_session_factory`, which creates a database and runs all migrations per invocation. Investigate separately with PostgreSQL timings and isolation validation. |

## Follow-up queue

1. Diagnose the API test stall before claiming repository-wide timing gains. The
   default local suite collected 6,901 tests but stopped making progress near
   `tests/unit/api/test_app.py`; it was interrupted. No full-suite pass is claimed.
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
