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

## Completed review

- Replaced namespace and autospecced service graphs with concrete service/cog subclasses and checked containers.
  Inference, finalization, revision, purge, and diagnostics recorders expose the production method signatures.
- Moved routine async callbacks to the public Discord recorder; process/worker registration records operations
  without starting unrelated background jobs. The service-container helper no longer special-cases mocks.
- Replaced mocked SQLAlchemy lock tests with two-session PostgreSQL exclusion and transaction-release checks.
- Removed duplicated command inventories, field/dependency/version inventories already covered by consumers,
  a constructor echo, and retired API absence guards. Kept import-isolation, rendering, wire, security,
  architecture, concurrency, and command-taxonomy contracts.
- Audited skips: all remain conditional on optional dependencies, platform support, or sandbox capabilities.
  No unconditional skip or xfail was added.
- Fixed failures exposed by validation: explicit relationship loading prevents nullable joins in tag locking;
  voter account lookup now belongs to application services. Regenerated the ignored localization template with
  just i18n-extract before architecture validation.
- Fixed the extension-registration test's collection-order dependency with a typed subscription recorder.
  Reaction dispatch remains covered by its owning tests.

tests/README.md documents the configured topology, support helpers, and the separate API fuzz campaign.
All service-graph and callback queues have been reviewed. The scan below is the remaining boundary inventory.

## Retained doubles

Each location below is deliberately retained. Interaction, message delivery, and context behavior use the shared
harnesses. Narrow SDK member, attachment, and channel doubles remain for class checks or small external values;
they do not manufacture bot or service graphs. Recorder calls are concrete test support, not mock constructors.

```text
packages/squid-ui-discord/tests/test_form_discord.py:204 — Discord Attachment metadata/download boundary.
packages/squid-ui-discord/tests/test_form_discord.py:209 — Discord Attachment metadata/download boundary.
packages/squid-ui-discord/tests/test_roles.py:101 — Discord Member class/role boundary.
tests/support/discord.py:331 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/accounts/test_mojang.py:43 — HTTP client session boundary.
tests/unit/accounts/test_mojang.py:57 — HTTP client session boundary.
tests/unit/accounts/test_mojang.py:66 — HTTP client session boundary.
tests/unit/artifacts/test_s3_store.py:18 — Boto3 session/client boundary.
tests/unit/artifacts/test_s3_store.py:19 — Boto3 session/client boundary.
tests/unit/artifacts/test_s3_store.py:21 — Boto3 session/client boundary.
tests/unit/bot/events/test_vote_outcome_handlers.py:133 — HTTP status/reason read by a Discord exception.
tests/unit/bot/submission/test_build_recalc.py:159 — Discord TextChannel eligibility check.
tests/unit/bot/submission/test_consent_banner.py:123 — Discord TextChannel identity and empty history iterator.
tests/unit/bot/submission/test_consent_banner.py:125 — Discord TextChannel identity and empty history iterator.
tests/unit/bot/test_app_main.py:70 — Process health-server context boundary.
tests/unit/bot/test_app_main.py:93 — OpenTelemetry span/context boundary.
tests/unit/bot/test_app_main.py:94 — OpenTelemetry span/context boundary.
tests/unit/bot/test_app_main.py:121 — Discord command qualified name for tracing.
tests/unit/bot/test_app_main.py:126 — OpenTelemetry span/context boundary.
tests/unit/bot/test_app_main.py:127 — OpenTelemetry span/context boundary.
tests/unit/bot/test_errors.py:143 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/bot/test_guild_state.py:51 — HTTP response used to construct a Discord exception.
tests/unit/bot/test_guild_state.py:129 — Raw Discord gateway event identifiers.
tests/unit/bot/test_guild_state.py:131 — Deleted Discord channel ID and guild.
tests/unit/bot/test_i18n.py:87 — gettext catalog boundary.
tests/unit/bot/test_message_lookup.py:21 — HTTP response used to construct a Discord exception.
tests/unit/bot/test_message_lookup.py:31 — Discord TextChannel identity with an explicitly failing fetch endpoint.
tests/unit/bot/test_message_lookup.py:63 — Fetched Discord message content and edit timestamp.
tests/unit/bot/test_reactions.py:407 — Raw Discord gateway event identifiers.
tests/unit/bot/test_reactions.py:409 — Raw Discord gateway event identifiers.
tests/unit/bot/test_reactions.py:565 — Discord message author identity.
tests/unit/bot/test_reactions.py:584 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/bot/test_reactions.py:614 — Raw Discord gateway event identifiers.
tests/unit/bot/test_starboard.py:161 — Discord member ID and role identifiers for recount filtering.
tests/unit/bot/test_starboard.py:162 — Discord member ID and role identifiers for recount filtering.
tests/unit/bot/test_starboard.py:163 — HTTP response used to construct a Discord exception.
tests/unit/bot/test_starboard.py:173 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/bot/test_starboard.py:178 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/bot/test_starboard.py:179 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/bot/test_starboard.py:180 — Discord reaction user/member identity, roles, or bot flag.
tests/unit/bot/test_welcome_relay.py:71 — Discord TextChannel type/identity boundary.
tests/unit/bot/utils/test_permissions.py:107 — Discord Member class/role boundary.
tests/unit/bot/utils/test_sticky_message.py:160 — HTTP status/reason read by a Discord exception.
tests/unit/bot/utils/test_web.py:33 — HTTP client session boundary.
tests/unit/media/test_ffmpeg_adapter.py:182 — Explicit subprocess cancellation injection.
tests/unit/notifications/test_delivery_worker.py:88 — Discord User cached by the real client; only its send endpoint is exercised.
tests/unit/notifications/test_delivery_worker.py:121 — HTTP response used to construct a Discord exception.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:100 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:101 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:108 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:109 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:131 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:132 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:150 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:157 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:181 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:194 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_nucleation_adapter.py:206 — Native Nucleation schematic/value boundary, including malformed native results.
tests/unit/schematics/infrastructure/test_worker_logging.py:87 — Worker OpenTelemetry context/shutdown boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:101 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:102 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:300 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:316 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:317 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:318 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:319 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:339 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:340 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:342 — Subprocess pipe, startup, or stdio boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:421 — Worker OpenTelemetry context/shutdown boundary.
tests/unit/schematics/infrastructure/test_worker_logging.py:490 — Explicit worker protocol response injection.
tests/unit/test_observability.py:53 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:93 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:96 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:97 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:223 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:224 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:225 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:226 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:227 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:228 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_observability.py:229 — OpenTelemetry instrumentation or shutdown boundary.
tests/unit/test_process_lifecycle.py:59 — Process logging/telemetry cleanup boundary.
tests/unit/test_process_lifecycle.py:66 — Process logging/telemetry cleanup boundary.
tests/unit/test_process_lifecycle.py:82 — Process body fault injection.
```

## Results

Completed on 2026-11-09. All acceptance checks pass, with the external capability skips listed below.
The final scan matches all 85 retained source locations exactly and finds no create_autospec calls.
All seven workspace src directories remain in Pyrefly's search path.

| Check | Outcome |
|---|---|
| Default suite | 5,902 passed, 1 skipped in 103.48 s |
| PostgreSQL and other integration tests | 602 passed, 4 skipped in 404.29 s |
| Pyrefly, via just typecheck | Zero errors |
| Ruff check and format | Installed commit hooks passed on all changed Python files |
| Migration heads | Single head: c9f4e7a2b5d8 |
| Whitespace and retained-double inventory | git diff --check passed; every scan match is documented |

The unit skip is for PostgreSQL ARRAY support, which the integration suite exercises against PostgreSQL.
The four integration skips require ffmpeg and ffprobe, which are unavailable in this environment. PostgreSQL,
the disposable API stack, and the optional schematic checks ran successfully.

Reproduction commands:

```sh
UV_CACHE_DIR=/tmp/squid-uv-cache just i18n-extract
UV_CACHE_DIR=/tmp/squid-uv-cache just typecheck
UV_CACHE_DIR=/tmp/squid-uv-cache uv run --locked alembic heads
UV_CACHE_DIR=/tmp/squid-uv-cache uv run --locked pytest --no-cov --durations=40 --basetemp=/tmp/audit-default-verified
UV_CACHE_DIR=/tmp/squid-uv-cache uv run --locked pytest --no-cov tests/integration --durations=20 --basetemp=/tmp/audit-final-integration
git diff --check
```

SQLite/thread and Docker checks ran outside the sandbox. A sandboxed run stalled in SQLite durability tests
and could not connect to Docker. The localization template is generated and ignored by Git; regenerate it
before running the catalog architecture check.
