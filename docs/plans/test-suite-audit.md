# Workspace test-suite audit

## Scope

This audit covers every default test path in `pyproject.toml`: `tests/unit`, `tests/architecture`, and the
`squid-reactivity`, `squid-storage`, `squid-ui`, `squid-ui-discord`, `squid-ui-slack`, `squid-ui-widgets`, and
`squid-replication` package tests. It supersedes PR-183 plan 13.

The goal is test quality, not a smaller suite. A test stays when it protects observable behaviour, security or
privacy, architecture, a wire format, persistence, concurrency, or a meaningful property. A rewrite is worth doing
when the new test would catch a realistic regression the old one misses, or would survive a refactor that breaks the
old one without changing behaviour. A rewrite that does neither is churn; leave the test alone.

## Acceptance

The audit is complete when:

- no `SimpleNamespace` stands in for a service graph, a bot, a cog, or a domain object. A namespace may still stand
  in for a small third-party value object (a raw gateway event, an HTTP response, a reaction user) when the code
  under test reads only a couple of attributes; each such use is listed in [Retained doubles](#retained-doubles);
- each remaining `Mock`, `MagicMock`, or `AsyncMock` is a narrow third-party or process boundary, or explicit fault
  injection, and is listed in [Retained doubles](#retained-doubles);
- every skip depends on the platform, an optional dependency, or a sandbox capability;
- Pyrefly, Ruff, `alembic heads`, default tests, and PostgreSQL integration tests pass, or an unavailable external
  capability is reported in [Results](#results).

Check the first two with:

```sh
grep -rn 'SimpleNamespace(' --include=*.py tests packages/*/tests packages/*/src/*/testing.py
grep -rnE '\b(Magic|Async)?Mock\(' --include=*.py tests packages/*/tests packages/*/src
```

and compare the output with the retained list.

## Disposition rules

| Finding | Disposition |
|---|---|
| Namespace or mock service graph | Rewrite as a typed, stateful fake that records domain operations. Subclass the concrete service; do not add testing-only protocols to production code. |
| Discord boundary double | Use a harness (see [Discord harnesses](#discord-harnesses)); inject faults through explicit error fields. |
| Exact export, constant, signature, or field inventory | Replace with a consumer import, invocation, serialization, or discovery test. Exception: the user-visible command surface (see below). |
| Historical absence guard | Delete, unless the removed value can still arrive through untrusted, persisted, migrated, or wire input, or reintroducing it would be dangerous (a privileged command, a secret in a public payload, a forbidden import). |
| Constructor echo or mirrored helper implementation | Delete; keep only validation, transformation, or round-trip behaviour. |
| Cross-layer duplicate | Keep pure behaviour at its owning layer and one composition case at each real adapter boundary. |
| Mocked repository or database session | Test a pure mapper or compiler, or use PostgreSQL; do not simulate SQLAlchemy result mechanics. |
| Routine worker collaborator | Use a recorder; keep mocks only for scheduler or process faults that cannot be represented as state. |
| Unconditional skip | Delete the dead test and document the standalone campaign or external limitation that replaces it. |

### Command surface

Renaming a slash command breaks users' habits and forces a resync, and a consumer test will not notice. Keep
`tests/unit/bot/test_command_taxonomy.py` as the one deliberate check of the public command surface and its
permission-node gating; update it in the same change that renames a command. Do not add other command inventories.

### Discord harnesses

There are two layers:

- `squid_ui_discord.testing` (`InteractionHarness`, `MessageHarness`, `ContextHarness`, `AsyncCallRecorder`) is the
  framework's public test support. Package tests and bot tests that exercise a screen or session without bot services
  use it directly.
- `tests/support/discord.py` wraps it with `BotServices`, permissions, localization, and error reporting. Bot tests
  that go through a cog, command, or event listener use this layer.

Add to the bot layer only what needs bot services; anything else belongs in the package harness, so other hosts can
use it too.

## Execution order

1. Replace Discord package callback mocks with the existing async recorder and use harness fault fields.
2. Replace bot service graphs with concrete service subclasses and checked service containers.
3. Review the remaining worker, repository, and boundary tests against the disposition rules; record retained doubles.
4. Run focused tests per batch, then default tests, PostgreSQL integration tests, Ruff, Pyrefly, and migration checks.
   Record capability failures separately from test regressions.

Each batch is committed independently after its focused checks. The remaining-work lists stay open until reviewed.

## Remaining work

State as of 2026-11-09: 5,920 tests collected. The original scan found `SimpleNamespace` in 69 modules and mocks in
85; these lists are what remains. Treat them as a work queue, not acceptance criteria; the greps above decide when
the audit is done.

### Namespace service graphs to rewrite

These pass a namespace in place of the bot, a cog, or an application service:

```text
tests/unit/bot/test_recount.py                                  bot, services, cogs
tests/unit/bot/test_starboard.py                                cog.service, cog._debouncer, guild, members
tests/unit/bot/submission/test_build_recalc.py                  bot graph and inference/proposal results
tests/unit/bot/voting/test_vote_reactions.py                    channel
tests/unit/bot/submission/test_ingestion.py                     attachments and message
tests/support/discord.py                                        reaction user
```

### Mock-heavy modules to review

Module and number of mock constructions. Most will become recorders or typed fakes. The likely retained boundaries
are noted.

```text
packages/squid-ui-discord/tests/test_devtools_runtime.py          2
packages/squid-ui-discord/tests/test_form_discord.py              2
tests/unit/bot/test_app_main.py                                  19  process wiring; ProcessHealthServer patch may stay
tests/unit/bot/test_components_v2_ui.py                          14  authorize/render/refresh -> typed fake
tests/unit/bot/test_log.py                                       12  bot and context -> ContextHarness
tests/unit/bot/test_extension_loading.py                          9  bot services -> tests.support.services
tests/unit/bot/events/test_vote_outcome_handlers.py               9  bot and message -> MessageHarness
tests/unit/bot/submission/test_consent_banner.py                  5
tests/unit/bot/submission/test_ingestion.py                       4
tests/unit/bot/submission/test_build_recalc.py                    3
tests/unit/bot/test_diagnostics.py                                3
tests/unit/bot/test_request_commands.py                           3
tests/unit/bot/submission/test_message_context.py                 2
tests/unit/bot/test_poll_wizard_panel.py                          2
tests/unit/bot/test_errors.py                                     1
tests/unit/bot/test_i18n.py                                       1
tests/unit/bot/test_starboard.py                                  1
tests/unit/bot/test_welcome_relay.py                              1
tests/unit/bot/utils/test_permissions.py                          1
tests/unit/bot/utils/test_web.py                                  1
tests/unit/schematics/infrastructure/test_worker_logging.py      20  stdio, subprocess, rlimit: process boundary
tests/unit/schematics/infrastructure/test_nucleation_adapter.py  11  Nucleation schematic object: third-party boundary
tests/unit/test_observability.py                                 11  OpenTelemetry instruments: third-party boundary
tests/unit/test_process_lifecycle.py                              4
tests/unit/test_runtime.py                                        4
tests/unit/accounts/test_mojang.py                                3  HTTP boundary
tests/unit/artifacts/test_s3_store.py                             3  S3 client boundary
tests/unit/persistence/test_advisory_locks.py                     2
tests/unit/api/test_app.py                                        1
tests/unit/media/test_ffmpeg_adapter.py                           1
tests/unit/submissions/test_inference_recovery.py                 1
```

### Skips

Every current skip is conditional: POSIX shell or rlimits, Linux `/proc`, symlink or loopback-socket permission in a
sandbox, optional extras (`nucleation`, `loro`, `pycrdt`, OpenTelemetry SDK), `ffmpeg` availability, PostgreSQL-only
types, and the development type checker. No action is needed unless a new unconditional skip appears.

### Documentation

Done: `tests/README.md` describes `tests/support`, the configured topology, and the standalone API fuzz campaign,
and states that the removed in-process Schemathesis test is not part of `just test`.

## Retained doubles

List each deliberately retained namespace or mock here as `path:line — reason` when its module is finished.

```text
packages/squid-ui-discord/tests/test_roles.py:101 — Discord Member boundary for role-based access.
tests/unit/bot/test_guild_state.py:51 — HTTP response status/reason for Discord exceptions.
tests/unit/bot/test_guild_state.py:129 — raw thread deletion event identifiers.
tests/unit/bot/test_guild_state.py:131 — deleted Discord channel identifiers.
tests/unit/bot/test_message_lookup.py:21 — HTTP response status/reason for Discord exceptions.
tests/unit/bot/test_message_lookup.py:31 — Discord TextChannel boundary with a failing fetch endpoint.
tests/unit/bot/test_message_lookup.py:63 — fetched Discord message content and edit timestamp.
tests/unit/notifications/test_delivery_worker.py:88 — Discord User send boundary.
tests/unit/notifications/test_delivery_worker.py:121 — HTTP response for a forbidden Discord send.
```

## Results

The audit remains incomplete. The first implementation batches replaced Discord callback mocks, checked bot service
graphs in bot tests, and namespace records in authorization and inferred-draft tests. The remaining queues above
still need review; this is not an acceptance sign-off.

The installed commit hooks passed Ruff checks and formatting for the committed batches. Pyrefly passed with zero
errors. `alembic heads` reports `c9f4e7a2b5d8`. Sandbox runs need `UV_CACHE_DIR=/tmp/squid-uv-cache`;
the package run stalled in SQLite durability tests under the sandbox but completed outside it. Docker also required
running outside the sandbox.

The default run found five failures in unchanged architecture tests: missing recount strings in the localization
template, a voting transport importing `squid.voting.infrastructure.accounts`, and joined loading on the `message`,
`display_unit_definition`, and `canonical_unit_definition` relationships. These need resolution before acceptance.

PostgreSQL integration failed in
`test_moderation_and_projection_request_share_a_transaction[False]`: the tag-definition query applies
`FOR UPDATE` to nullable outer joins introduced by eager unit relationships. Four media tests skipped because
ffmpeg/ffprobe are unavailable. The run stopped at the first failure, so subsequent integration tests remain unchecked.
The final double scan still finds namespaces in 10 modules and mocks in 35 modules; some are retained boundaries
and the rest are covered by the open queues above.

Run `uv run --locked pytest --no-cov --durations=40 --basetemp=<writable dir>` so a sandboxed temp directory does
not show up as setup errors.

| Run | Collected | Outcome | Duration | Notes |
|---|---:|---|---:|---|
| Baseline, 2026-08-29 | 4,823 | 4,687 passed, 10 failed, 10 skipped, 118 setup errors | 276.22 s | Setup errors came from the sandbox-denied system temp directory. Nine failures were a since-fixed permission-node hashing regression; one was the UI literal localization guard. |
| First callback batch, 2026-11-09 | 460 | Passed | 4.77 s | Focused Discord and bot tests. |
| Final service batch, 2026-11-09 | 28 | Passed | 1.88 s | Includes schematic access with a real DoorBuild. |
| Inferred draft options, 2026-11-09 | 2 | Passed | 0.88 s | Real FormOptionSet values. |
| Default suite, 2026-11-09 | 5,920 | 5,914 passed, 5 failed, 1 skipped | 103.43 s | Architecture failures described above. |
| PostgreSQL integration, 2026-11-09 | 604 | 533 passed, 1 failed, 4 skipped; stopped early | 356.79 s | First-failure run; outer 360-second timeout expired during shutdown. |
