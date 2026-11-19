# Standalone function ownership audit

Reviewed 2026-11-16 at `a8d616f00`. The existing-class recommendations below
have since been implemented, including the consent, taxonomy, and timing-loader
follow-ups. The proposed new `DiscordDraftIntake` adapter remains deferred.
The findings retain the original function names and locations for reference.
Three subagents reviewed Discord UI, domain/application code, and infrastructure;
the primary review covered shared bot helpers and checked the leading findings.
The scope is `squid/`, with emphasis on bot workflows and their supporting services.
Workspace libraries, migrations, and test helpers were not exhaustively audited.

There are worthwhile moves, but function count is not itself a design problem.
Prioritize behavior that interprets an object's rules, uses its dependencies, or
constructs that specific type. Moving a pure function into a static-method namespace
does not establish useful ownership. Locations below refer to the reviewed revision.

## Prioritize these changes

### Field validation belongs with the field

In `squid/core/submissions/domain/forms.py`, move `_validate_value` (473) to
`FormField.validate_value(value)` and `_field_for` (462) to
`FormField.for_purpose(purpose)`. The former reads the field's kind, constraints,
options, and ID; the latter applies its edit rules. `FormField` already owns
visibility and definition validation.

Callers are `FormManifest.validate_answers` and `_sections_for`. Keep defaults,
missing required answers, unknown fields, and visibility orchestration on the
manifest. Dynamic option validation remains separate. Preserve the reason codes.

### Repository operations should use their repository's dependencies

In `squid/core/submissions/infrastructure/finalization_repository.py`, move
`_locked_job` (475), `_claimed_job` (485), `_complete` (560), and
`_current_schematics` (605) into `PostgresFinalizationJobRepository`.
Fold `_require_current_media` (450) into the existing `require_current_media`
method, which currently only forwards to it.

Production callers are repository methods or this helper cluster. They implement
the repository's claim, completion, and artifact rules using its session. Preserve
the current transaction and lock order; use the session provider within the active
unit of work, rather than caching a session across operations. Keep `_locked_draft`
and `_require_expected_draft` shared: other repositories import them.

The same argument applies to `_membership_changed` (618),
`_lock_mutable_submission_draft` (632), and `_active_totals` (661) in
`squid/core/media/infrastructure/repository.py`. Only `PostgresMediaJobRepository`
operations use them, through `enqueue`, `discard`, and `attach`. They enforce
draft mutation and capacity rules under lifecycle locks.

### A compiled expansion should expose its evaluation

In `squid/core/builds/domain/expansion_evaluation.py`, move `evaluate_expansion` (278)
to `NormalizedExpansion.evaluate(opening)` and `normalize_expansion` (147) to
`NormalizedExpansion.from_source(source)`. The compiled type's principal operation
and constructor currently sit beside it.

Callers are record live evaluation, expansion-region analysis, door-category
selection, and expansion tests. Keep arithmetic helpers standalone. Do not move
the solver onto `DoorExpansion`, which represents source facts independently of
the solver. This improves API cohesion; the existing functional API is also viable.

## Small, cohesive follow-ups

| Current function | Proposed owner | Caller evidence and benefit |
| --- | --- | --- |
| `squid/bot/voting/poll_wizard.py:326` `_draft` | `PollDraft.from_answers` | `_review` and `PollScreen._finish` construct the same bot-local type, which already owns normalization and validation. |
| `squid/bot/voting/publisher.py:182` `_emoji_is_usable` | `DiscordPollPublisher._emoji_is_usable` | `resolve_options` wraps it in a lambda to supply `self._bot`; a bound method uses the existing dependency directly. |
| `squid/bot/submission/ui/drafts.py:997` `draft_editor` | Async `DraftEditorScreen.load` | `open_draft` and `BuildSubmitCommands` use this factory to load a manifest and initialize one screen. Keep consent and presentation orchestration outside the constructor. |
| `squid/core/accounts/domain/profiles.py:264` `present_public_profile` | `PublicCreatorProfile.from_record` | `AccountService` constructs this public projection. Both types already live in the same layer; retain hidden-profile and avatar privacy rules. |
| `squid/core/records/application/published_points.py:67` `published_door_point` | `PublishedDoorPoint.from_publication` | The range-publication service constructs this projection; adjacent `RangeAttemptSummary.from_attempt` uses the same pattern. Preserve unpublished and outside-query distinctions. |
| `squid/core/schematics/infrastructure/wire.py:165` `decode_worker_request` | `WorkerRequest.from_frame` | `worker_main` validates and constructs this wire-layer type. Keep payload validation and `FrameStreamClosed` translation intact. |

These constructor moves improve discoverability more than encapsulation. They
can be independent small commits, with imports and direct test calls migrated.
In particular, the poll publisher test imports `_emoji_is_usable` directly.

## Lower-priority possibilities

- `squid/bot/accounts/consent.py:140` `_consent_prompt` could become named
  `ConsentPrompt` constructors for account consent and identity linking. Keep
  continuation orchestration standalone; tests directly import the factory.
- `squid/core/builds/infrastructure/taxonomy.py:124` `_resolve_official_tag_rows`
  could become a private `PostgresBuildTaxonomy` method. Its only caller is
  `resolve_official`; pure name-matching helpers should remain separate.
- `squid/core/records/infrastructure/repository.py:782,806` `_door_timings` and
  `_extender_timings` could become private `PostgresRecordRepository` loaders.
  Only its listing methods call them; the practical benefit is modest.
- `squid/bot/submission/draft_intake.py` could become a `DiscordDraftIntake`
  adapter owning the intake dependency and download policy. Unlike the moves
  above, this needs a new object and wiring; defer until that boundary is useful.

## Keep these standalone

- Extension `setup` functions, route handlers, decorators, and registered UI
  callbacks have framework-defined roles. Their shape is not an ownership defect.
- `present_chunk_form` and `present_add_files` serve multiple screens. Putting
  them on a workspace would mix UI interaction into answer storage.
- Profile/build rendering and `to_message_snapshot` are transport adapters.
  Adding them to shared domain types would introduce dependencies in the wrong
  direction. The Discord account-ID helper likewise accepts Discord objects.
- Permission resolution combines actors, rules, roles, and catalogues; no one
  input is an obvious owner. `match_choice` resolves across a collection.
- `ensure_server_settings` and the shared draft-lock helpers serve multiple
  repositories within their callers' transactions.
- `start_log_capture` and `start_permission_epoch_watch` compose independent
  services. The general background supervisor should not know those services.

## Validation for implementation

For each change, search imports, callback registrations, package re-exports, and
test patch targets. Run the existing focused tests with `--no-cov`, then the
project type check and installed hooks. Repository moves additionally need the
relevant integration tests to protect locking and transaction behavior; constructor
moves should retain privacy, validation, and error-path coverage.

The original audit changed no executable code. The implementation preserves the
existing validation, privacy, and transaction rules; callers, exports, and test
patch targets now use the owning classes. Focused unit and PostgreSQL integration
tests cover the changed behavior.

The final project type check passed with zero errors. Regenerated localization
catalogs pass validation, with existing Chinese translation review notices.

Architecture validation passed 39 checks and found three existing failures:
process-environment access in the range analyzer, builtin exceptions in record
application code, and a record-model import of application evidence types. All
three failures were reproduced from pre-refactor revision `9c7584704` in an
isolated source export. They are outside this ownership refactor.
