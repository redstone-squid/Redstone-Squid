# Workspace test-suite audit

## Scope and acceptance

This audit covers every configured default test path in `pyproject.toml`: the bot and the
Squid UI, Discord, Slack, widgets, reactivity, replication, and storage packages. It is a
quality audit, not a coverage-reduction exercise. Tests stay when they protect observable
behaviour, security or privacy, architecture, wire formats, persistence, concurrency, or a
meaningful property.

The starting inventory supplied for this audit was 423 test files, 86,674 lines, 4,221 test
definitions, 349 `SimpleNamespace` constructions, and 595 mock constructions. A fresh collection
on 2026-08-29 found 4,823 items, with two modules skipped at collection time. The pre-cleanup
`--no-cov --durations=40` result and the matching final result are recorded in [Results](#results).

Acceptance is behavioural:

- no `SimpleNamespace` remains in tests or shipped test-support modules;
- each remaining `Mock`, `MagicMock`, or `AsyncMock` is a narrow third-party boundary or explicit
  fault injection and is listed below;
- no unconditional skip, exact export inventory, or assertion whose sole purpose is proving that
  removed code remains absent remains;
- framework distribution validation, Pyrefly, Ruff, migration heads, default tests, and PostgreSQL
  integration tests pass, or an unavailable external capability is reported explicitly.

## Disposition rules

| Finding | Disposition |
|---|---|
| Namespace or mock service graph | Rewrite as a typed, stateful fake with domain-specific records. |
| Discord boundary double | Use the public `InteractionHarness`, `MessageHarness`, or `ContextHarness`; inject faults through explicit error fields. |
| Exact export, command, constant, signature, or field inventory | Replace with a consumer import, invocation, serialization, or discovery workflow. |
| Historical absence guard | Delete unless the value can still arrive in an untrusted, persisted, migrated, or durable wire input. |
| Constructor echo or mirrored helper implementation | Delete; retain only validation, transformation, or round-trip behaviour. |
| Cross-layer duplicate | Keep pure behaviour at its owning layer and one composition case at each real adapter boundary. |
| Mocked repository or session | Test a pure mapper/compiler or use PostgreSQL; do not simulate SQLAlchemy result mechanics. |
| Routine worker collaborator | Use a recorder; mocks remain only for scheduler/process faults that cannot be represented as state. |
| Unconditional skip | Delete dead coverage and document the maintained standalone campaign or external limitation. |

## Flagged modules

The initial scan found `SimpleNamespace` in 69 modules and mock classes in 85 modules. Some modules
appear in both groups. These are the owning dispositions; the final scan records any deliberately
retained mock at the individual call site.

### Discord framework and shared support

Rewrite the shipped Discord doubles and every consumer to typed harnesses; retain payload queries,
limit assertions, rendering helpers, and deterministic scheduler draining. Split message-root
coverage by public dispatch, delivery, lifecycle, renewal, and failure behaviour after removing
engine-level and private-staging duplicates.

```text
packages/squid-ui-discord/src/squid_ui_discord/testing.py
packages/squid-ui-discord/tests/test_adoption.py
packages/squid-ui-discord/tests/test_devtools.py
packages/squid-ui-discord/tests/test_devtools_runtime.py
packages/squid-ui-discord/tests/test_form_discord.py
packages/squid-ui-discord/tests/test_invocation.py
packages/squid-ui-discord/tests/test_message_payload.py
packages/squid-ui-discord/tests/test_message_root.py
packages/squid-ui-discord/tests/test_message_root_scheduler.py
packages/squid-ui-discord/tests/test_pagination.py
packages/squid-ui-discord/tests/test_roles.py
packages/squid-ui-discord/tests/test_runtime.py
packages/squid-ui-discord/tests/test_screen.py
packages/squid-ui-discord/tests/test_session_specs.py
packages/squid-ui-discord/tests/test_sessions.py
tests/helpers/discord.py
```

### Bot screens and Discord adapters

Rewrite screens around rendered public controls and recorded domain operations. Introduce narrow
account, settings, catalogue, moderation, submission, and worker ports only where production
constructors currently force an oversized concrete service. Retain privacy, permission, payload,
and composition boundaries; remove repeated framework transaction, history, invalidation,
pagination, and confirmation claims.

```text
tests/unit/bot/events/test_dispatcher.py
tests/unit/bot/events/test_handlers.py
tests/unit/bot/events/test_vote_outcome_handlers.py
tests/unit/bot/posts/test_reconciler.py
tests/unit/bot/posts/test_vote_renderer.py
tests/unit/bot/submission/test_build_edit_command.py
tests/unit/bot/submission/test_build_recalc.py
tests/unit/bot/submission/test_consent_banner.py
tests/unit/bot/submission/test_ingestion.py
tests/unit/bot/submission/test_mention_fallback_search.py
tests/unit/bot/submission/test_message_context.py
tests/unit/bot/submission/test_search_command.py
tests/unit/bot/submission/test_submission_form.py
tests/unit/bot/test_account_panel.py
tests/unit/bot/test_app_main.py
tests/unit/bot/test_claim_review.py
tests/unit/bot/test_command_autocomplete_wiring.py
tests/unit/bot/test_components_v2_ui.py
tests/unit/bot/test_consent_gate.py
tests/unit/bot/test_devtools_cog.py
tests/unit/bot/test_diagnostics.py
tests/unit/bot/test_errors.py
tests/unit/bot/test_extension_loading.py
tests/unit/bot/test_help_screen.py
tests/unit/bot/test_i18n.py
tests/unit/bot/test_layout_showcase.py
tests/unit/bot/test_log.py
tests/unit/bot/test_message_context_actions.py
tests/unit/bot/test_notification_screen.py
tests/unit/bot/test_operations.py
tests/unit/bot/test_poll_wizard_panel.py
tests/unit/bot/test_reconciler.py
tests/unit/bot/test_records_screen.py
tests/unit/bot/test_reply_visibility.py
tests/unit/bot/test_settings_panel.py
tests/unit/bot/test_starboard.py
tests/unit/bot/test_starboard_screen.py
tests/unit/bot/test_tags_screen.py
tests/unit/bot/test_topics.py
tests/unit/bot/test_version_screen.py
tests/unit/bot/utils/test_permissions.py
tests/unit/bot/utils/test_sticky_message.py
tests/unit/bot/utils/test_web.py
```

### API, application services, workers, and infrastructure

Replace transport service graphs and routine worker mocks with typed stateful fakes. Consolidate
delegation-only wrapper tests, while retaining validation, mapping, ordering, retries,
idempotency, and failure policy. Repository/session mocks are rewritten as pure mapping tests or
PostgreSQL integration coverage.

```text
tests/unit/accounts/test_mojang.py
tests/unit/api/fakes.py
tests/unit/api/test_app.py
tests/unit/api/test_auth_routes.py
tests/unit/api/test_authoritative_build_views.py
tests/unit/api/test_build_writes.py
tests/unit/api/test_catalogue_contract.py
tests/unit/api/test_creator_routes.py
tests/unit/api/test_me_routes.py
tests/unit/api/test_notifications.py
tests/unit/api/test_phase2_reads.py
tests/unit/api/test_profile_routes.py
tests/unit/api/test_search_routes.py
tests/unit/api/test_suggest_routes.py
tests/unit/api/test_vote_writes.py
tests/unit/auth/application/test_web.py
tests/unit/builds/application/test_services.py
tests/unit/builds/infrastructure/test_embeddings.py
tests/unit/builds/infrastructure/test_locks.py
tests/unit/builds/infrastructure/test_taxonomy.py
tests/unit/builds/infrastructure/test_text_generation.py
tests/unit/cli_auth/test_api_contract.py
tests/unit/cli_auth/test_security.py
tests/unit/events/test_listener.py
tests/unit/events/test_service.py
tests/unit/minecraft_auth/test_api_contract.py
tests/unit/minecraft_auth/test_security.py
tests/unit/notifications/test_application.py
tests/unit/records/infrastructure/test_repository.py
tests/unit/schematics/infrastructure/test_worker_logging.py
tests/unit/schematics/test_public_api.py
tests/unit/search/test_projection.py
tests/unit/submissions/test_media_api_contract.py
tests/unit/suggestions/test_catalogue.py
tests/unit/sync/test_service.py
tests/unit/test_observability.py
tests/unit/test_runtime.py
tests/unit/voting/test_dynamic_voting.py
tests/unit/worker/test_events.py
tests/unit/worker/test_idempotency_retention.py
tests/unit/worker/test_media_cleanup.py
tests/unit/worker/test_schematic_pool_health.py
tests/unit/worker/test_submission_draft_expiry.py
```

## Documentation disposition

This audit supersedes the limited follow-up described by PR-183 plan 13. That earlier work closed
the review threads it named, but explicitly left the roughly twenty then-known namespace service
graphs for later and retained a command-tree snapshot. The workspace audit applies the broader
consumer-behaviour rule consistently and records the final outcome here.

`tests/README.md` must describe `tests/support`, the real configured topology, and the standalone
API fuzz campaign. The removed in-process Schemathesis test must not be presented as part of
`just test`; every surviving skip must correspond to a platform or optional-dependency boundary.

## Results

| Run | Collected | Outcome | Duration | Notes |
|---|---:|---|---:|---|
| Before cleanup | 4,823 | Running | - | `uv run --locked pytest --no-cov --durations=40` |
| After cleanup | - | Pending | - | Same command and environment |

The audit is complete only when every flagged module above has a final rewrite, delete, or
retained-boundary disposition reflected in the code and the final validation result is recorded.
