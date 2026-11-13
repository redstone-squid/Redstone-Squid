# Test suite necessity and performance audit

Completed: 2026-11-13.

## Scope and result

Reviewed all 642 tracked test files in the [inventory](test-suite-audit-inventory.csv),
including the default Python suite, database/infrastructure integration tests,
web unit and browser tests, Minecraft/Kotlin tests, embedded Rust tests, and the
Node deployment-script test. The inventory counts 5,813 remaining test declarations;
parameterized cases are not counted separately. Every row has a review disposition.
Concurrent additions were included through the final API/DTO review checkpoint.

This completes the individual necessity review and accepted cleanup work. It does
not mean every suite was executed, or that every future optimization has been
exhausted. The inventory records decisions against the source snapshot reviewed;
subsequent changes to an existing test body require fresh review.

The [November 9 audit](test-suite-audit-2026-11-09.md) remains the historical record
of the earlier typed-fake, Discord-harness, and retained-double cleanup. Its
validation results describe that earlier revision, not this one.

## Review method

Three smaller-model agents reviewed bounded areas by reading test bodies and
relevant implementation and overlapping coverage. The parent reviewed proposed
changes, rejected unsupported deletions, applied edits, ran focused checks, and
committed separate batches. File inventories and assertion scans alone did not
count as review. New or renamed test functions were checked against the reports
before the final inventory was closed.

A test stays when it protects behavior, a boundary, privacy, persistence,
concurrency, or a meaningful property. Type narrowing, nonempty checks that
prevent vacuous assertions, fresh-process import checks, backend differences,
and malformed-input properties remain. Assertion count is not a runtime proxy.
No property-test budgets were reduced merely to obtain a faster run.

## Main classes of findings

| Class | Changes and retained protection |
| --- | --- |
| Oversized fixtures | Reduced a pagination fixture from 2,000 lines to 20 long lines while keeping genuine overflow. Five facet-completion tests now seed twelve documents instead of 2,005; the index-plan test retains the large dataset. |
| Unnecessary setup | Thirteen SQL/Python backoff comparisons now run in one PostgreSQL query without application migrations. Consolidated empty-queue checks, repeated configuration loads, and duplicate planning/rendering. |
| Duplicate coverage | Removed duplicate catalog validation and overlapping architecture scans, repeated formatter/command-registration checks, constructor/helper echoes, and subset creator-handler checks already covered through HTTP. |
| Vacuous assertions | Replaced nonempty/substring or self-referential expectations with parsed output, exact mappings, explicit snapshots, and actual resource-delivery effects. |
| Unexercised branches | Session inspection now creates a session; shutdown tests actually finish the root; file tests contain assets; field fallback tests create shared-budget pressure; unsupported Minecraft control tests include that control. |
| Fakes masking failures | Cache loaders yield so concurrent misses overlap. Vote attachment changes publish only on commit. Fake-only prefix-filter coverage was removed while real query delegation and PostgreSQL behavior remain. |
| Incidental timing | Core concurrency checks use explicit synchronization. Retained real Redis expiry, heartbeat, cancellation and TTL tests where elapsed time is part of the behavior. |
| Obsolete code | Removed `is_sane_database`, its unused inspection helpers, and the entire sanity-check test module at the user's request; there were no other callers. |

Other accepted changes include deterministic verification-code boundary tests,
removing repeated duration-parser cases already covered by shared fixtures,
unconditional Rust profile assertions, exact Paper installation headers, and
removing compressed-byte checks implied by a complete golden payload.

Some proposed deletions were rejected. Examples include the build-field combine
test, which catches omitted fields in an explicit mapping; version-repository
rollback, which protects that repository's transaction boundary; distinct
PostgreSQL races and constraints; and the independent atomic-resource scenario.
The latter now requires one `LR` delivery, one load per resource, and no edit.
It makes no claim that separate atomic reads are discovered simultaneously.

## Validation

| Check | Result |
| --- | --- |
| Final broad default run | 6,806 passed and 2 failed in 118.15 s, without coverage. Both failures were subsequently repaired and checked in focused runs; the entire suite was not rerun. |
| Broad-run failure: generated catalogs | Refreshed English catalogs from current declarations. Both catalog tests passed in the focused rerun. Most generated changes were source-location metadata; obsolete browsing strings were removed. |
| Broad-run failure: session inspector | Corrected Markdown escaping for random root IDs. All 32 inspector tests passed afterward. |
| Whole-project `just typecheck` | Zero errors after the final resource repair and concurrent API changes. |
| Final creator/API/DTO batch | 56 passed. |
| Discord duplicate-removal batch and retained core coverage | 375 passed. |
| Atomic resource repair | 13 passed. |
| PostgreSQL backoff, queue-health and diagnostics changes | 7 passed against the local PostgreSQL container. |
| PostgreSQL facet fixture reduction | 6 passed, including the retained index-plan assertion. |
| Other focused Python batches | Configuration/runtime/service graphs (92), builds/submissions (355), bot/UI/i18n/widgets (152), notifications and related services (113), suggestions/sync (84), community/diagnostics/voting (73), plus smaller area checks passed. |
| Other languages and adapters | Web unit tests (152), the changed Chromium detail scenario, Rust CLI library (10), and three affected Minecraft modules using JDK 25 passed. |
| Formatting | Installed Ruff and whitespace commit hooks passed on changed files. |

The complete database, Docker, browser, Rust, and Minecraft suites were not rerun
as part of this final pass. In particular, the redundant enum-name assertion
removed from build integration was reviewed without executing that integration
module. Threaded API/S3 and Docker checks ran outside the restrictive sandbox;
sandbox stalls were not treated as evidence that those tests were unnecessary.

An earlier full default run had 6,852 passes, one skip and one failure in 125.61 s.
The failure was an outdated optional-None expectation and was fixed. Concurrent
application changes and the different suite contents make those timings unsuitable
as a controlled before/after performance comparison.

## Performance evidence and separate follow-ups

The first isolated Discord package runs measured 49.43 s before and 25.81 s after
the pagination-fixture cleanup, with 1,893 and 1,890 passing cases respectively.
These were single local runs. Later reductions have concrete setup savings, but
no additional controlled suite-wide speedup is claimed.

The last broad run's slowest tests were the fresh-layout comparison benchmark
(8.29 s) and representative-change benchmark (5.00 s). Their cache-path assertions
remain meaningful. Investigate benchmark setup and garbage collection before
reducing samples or removing them.

Separate future work may measure migrated-database setup and evaluate isolated
cloned templates. Preserve migrations, triggers, constraints, real commits and
per-test isolation; replacing migrations with `create_all` is not equivalent.
Additional Rust/Minecraft boundary-coverage suggestions are recorded as deferred
additions in the inventory. They are new coverage proposals, not unfinished
necessity review or accepted cleanup.
