# Close the remaining schema-audit findings

> **Status (2026-11-07).** Phase 0 is landed. Phases 1-6 are open; nothing below has started.
> Every design decision is made and recorded inline as **Decision**; Phase 5 is deliberately
> deferred. Nothing is left to investigate before implementing. File references were refreshed
> against the current tree on the same date; line numbers drift, so search for the named symbol if
> one is off.

## Context

An audit of the application tables and their SQLAlchemy models turned up nineteen findings. Ten
landed directly (Phase 0). The rest need a design decision, an application change, or both. Four
of the original findings turned out to be wrong once looked at closely; those corrections are kept
below because each one was reported as an easy fix.

**Operating assumptions**, confirmed with the maintainer:

- **Migrations are applied offline.** Workers are stopped, so no expand/contract sequencing is
  needed. Use plain `CREATE INDEX`/`ALTER TABLE`, not the `CONCURRENTLY` variants.
- **Existing data is not precious.** Backfills may be lossy, and constraints may be added directly
  instead of `NOT VALID` followed by validation.
- **Foreign keys are immediate.** Make an FK `DEFERRABLE` only when the write order genuinely cannot
  be fixed (a cycle, or a subtype check like the vote-session triggers in
  `squid/persistence/postgres_entities.sql`). A deferred FK reports its violation at commit, far
  from the statement that caused it, so it hides ordering bugs instead of fixing them.

**The correctness gate is `test_migrations_create_schema_without_drift`**
(`tests/integration/test_alembic_migrations.py`). It runs the whole migration chain against a clean
PostgreSQL container and then runs `alembic check`. It **passes** as of 2026-11-07; the 13
`modify_comment` diffs recorded earlier have since been fixed. Every phase must keep it passing.

A trap when documenting models: `Base.__init_subclass__` (`squid/persistence/base.py`) turns an
attribute docstring into a column comment, so prose on a `mapped_column` is a schema change. Use a
`#` comment for notes about the mapping.

## Phase 0 — Landed — **DONE**

Hashes as of 2026-08-18. The branch has been rebased since the fixes first landed, so older hashes
no longer resolve; the content was re-verified unchanged.

| Commit | Finding |
|---|---|
| `5061234e` | `Identity()` on three foreign-key columns in `votes`, `build_vote_sessions`, `delete_log_vote_sessions` |
| `e8dbbada` | `build_edit_history` dropped outright (single-row-per-build primary key, redundant unique, identity on the FK, and nothing ever wrote to it) |
| `9c12b7b4` | `tag_definitions_numeric_metadata_check` admitted every row |
| `4eaaa220` | `messages.id` and `server_settings.server_id` declared `SERIAL` for externally assigned snowflakes (model-only fix) |
| `ac4f91a2` | 37 foreign-key indexes, chiefly the `accounts` erasure fan-out, plus `passive_deletes=True` on the four `Build` collections |
| `53877d09` | `api_keys.created_by_account_id` ON DELETE; `starboards.colour` width; three nullable `created_at` columns |

Migration chain: `c4e8f2a1b6d3` → `d5f9a2c7b481` → `e6a0b3d8c592` → `f7b1c4e9d6a3` → `a8c2d5f0e7b4` →
`b9d3e6a1f8c5`.

Leftover: `schema/structure/public/tables/build_edit_history/` is still tracked in git although the
table is gone. It is a stale dump, not a schema defect; delete it in a cleanup commit.

## Corrections to the original audit

**`votes.option_id` cannot take a foreign key on its own.** A vote resolves to an *emoji alias row*,
not to an option identifier: `_close_at_threshold` (`squid/voting/infrastructure/repository.py`)
joins on `(vote_session_id, identifier, emoji)` plus "same guild or the global row". Two things
block a direct FK:

- `identifier` is not unique even within one guild, because several aliases share it (👍 and ✅
  both select `approve`).
- A vote cast in guild *G* legitimately matches an alias stored as global, so a key that includes
  the vote's guild rejects valid votes.

Phase 2 fixes this with a surrogate key on the alias row.

**A composite FK containing a nullable column is vacuous.** Under `MATCH SIMPLE`, a NULL in any
referencing column satisfies the constraint without checking it. This rules out the
`record_definitions` → `record_competitions` composite FK (`version_id` is NULL for `all_time`
scope, which are exactly the rows most likely to drift; `MATCH FULL` would reject them instead), and
it equally rules out "turn the guild `0` sentinel into NULL, then add a composite FK".

**`domain_event_deliveries` does not carry a duplicate counter.** `attempts` counts failures and
`claim_count` counts claims, so they measure different things. The real inconsistency is elsewhere;
see Phase 3.

**`media_artifacts.object_key` has no ordering problem.** The worker calls `track_artifacts()` and
commits the `media_artifact_objects` rows *before* uploading bytes
(`squid/media/application/jobs.py`, "Commit leases before writing bytes…"). `complete()` inserts
the artifact rows later, and cleanup only soft-deletes object rows (`deleted_at`), so an immediate FK
holds.

## Phase 1 — Mechanical schema corrections

Independent of each other; any can go first.

- **Name every constraint from one convention.** `Base.metadata` has no `naming_convention`
  (`squid/persistence/base.py`), so some constraints carry PostgreSQL-invented names while the rest
  are named by hand. Counted from `Base.metadata` on 2026-11-07, there are **39**: 33 foreign keys
  (all of `starboard_*`, most `submission_*` tables, `media_draft_references`,
  `generic_vote_sessions`, `domain_event_deliveries`, and the `guild_id` FKs on `guild_vote_emojis`
  and `guild_vote_role_weights`), the three `server_settings` channel uniques, and three checks.
  Every one of the FKs and uniques is single-column.
  **Decision:** set `naming_convention = {"ix": "ix_%(column_0_label)s",
  "fk": "%(table_name)s_%(column_0_name)s_fkey", "uq": "%(table_name)s_%(column_0_name)s_key",
  "pk": "%(table_name)s_pkey"}`. `ix` is SQLAlchemy's existing default and the other three are
  PostgreSQL's own defaults, so the 36 unnamed FKs and uniques keep their deployed names and no
  migration is needed (none of the generated names exceeds 63 characters, where the two would
  truncate differently). Leave out a `ck` key: SQLAlchemy feeds explicit names into a
  `%(constraint_name)s` template and would prefix the table name onto every hand-named check.
  Instead, give the three checks explicit names equal to their deployed ones:
  `guild_vote_emojis_choice_check`, `guild_vote_emojis_kind_check`,
  `guild_vote_role_weights_kind_check`. Checked against a copy of `Base.metadata` on 2026-11-07,
  this convention renames no hand-named constraint. The drift test confirms it after the change.

- **Constrain `builds.category`.** It is the `polymorphic_on` discriminator (`squid/builds/models.py`)
  but is nullable with no CHECK, and `Build` has no base `polymorphic_identity`, so a row with a NULL
  or unknown category cannot be loaded. Add `NOT NULL` and
  `CHECK (category IN ('Door','Extender','Utility','Entrance','Other'))`, matching `BuildCategory`
  (`squid/builds/domain/models.py`). Also drop the `category IS NOT NULL` predicate from
  `idx_builds_category`.
  **Decision:** backfill NULL rows to `'Other'` and insert the matching `other_builds` row so
  joined-table inheritance stays consistent.

- **Guard `permission_role_includes` against cycles in the database.** `_would_cycle`
  (`squid/permissions/application/administration.py`) is a read-then-write with no lock, so two
  concurrent `add_include` calls that are each acyclic can together commit a cycle. The database
  only checks `role_id <> included_role_id`. Add a `CONSTRAINT TRIGGER` that runs a recursive CTE to
  `squid/persistence/postgres_entities.sql`, and update the counts in
  `squid/persistence/alembic_entities.py` (`EXPECTED_FUNCTIONS`, `EXPECTED_TRIGGERS`; currently 15 and
  46).

- **Object-key integrity in media and schematics.**
  - **Decision:** add an immediate FK `media_artifacts.object_key` →
    `media_artifact_objects.object_key`. No worker change is needed (see the corrections above).
  - **Decision:** `schematic_files.object_key` is always `schematics/{sha256[:2]}/{sha256}`
    (`squid/schematics/application/storage.py`). Instead of a UNIQUE, add
    `CHECK (object_key = 'schematics/' || left(sha256, 2) || '/' || sha256)`. The primary key on
    `sha256` then makes the key unique as well, and the check also rejects a key that doesn't match
    its digest.

## Phase 2 — Surrogate key for vote options

Unblocks the `votes` → option foreign key.

`vote_session_options` has primary key `(vote_session_id, guild_id, emoji)`, with `guild_id = 0`
meaning "global alias". The domain already models this as `None` (`VoteOption.guild_id`,
`VoteSessionSnapshot.options_for_guild` in `squid/voting/domain/models.py`). Only the repository
translates it to `0` (`guild_id or 0` on insert, `guild_id == 0` in `_close_at_threshold`).

**Decision:** give the alias row a surrogate key and point votes at it.

1. `vote_session_options`: add `id` (identity) as the primary key. Make `guild_id` nullable with no
   default, migrate `0` → NULL, and add `UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, emoji)`
   to keep the old uniqueness. Add `UNIQUE (vote_session_id, id)` as the FK target.
2. `votes`: add `option_alias_id` with a composite FK `(vote_session_id, option_alias_id)` →
   `vote_session_options (vote_session_id, id)`. Every referencing column is non-null, so the FK is
   actually enforced, and the session column stops a vote from pointing at another session's alias.
   Backfill using the existing guild-or-global join; drop votes that don't resolve. Then drop
   `votes.option_id` and `votes.emoji`, which the alias row now supplies.
3. `votes.guild_id` stays, because `VoteSelection` carries it into the domain as the guild the
   reaction was made in. Make it nullable instead of `0`. **Decision:** no FK to
   `server_settings`. Settings rows are created only by `on_guild_join`
   (`squid/settings/application/services.py`, `guild_joined`), so guilds the bot joined while
   offline have none, and an FK would make casting a vote depend on that. Once votes reference the
   alias row, scoring no longer reads `votes.guild_id`, so the column is a recorded Discord fact like
   a channel id, not a reference that needs enforcing.
4. Repository: write `option.guild_id` as-is, resolve the alias id when casting a vote, and replace
   the guild-or-global join in `_close_at_threshold` and the snapshot loader with a join on
   `option_alias_id`.

**Not changing:** `starboard_sources.channel_id = 0` ("every channel in the guild"). Channels are
not a table, so a NULL buys no foreign key. Retiring the sentinel would need the same surrogate-key
change for consistency alone. Revisit only if the starboard tables are reworked for another reason.

## Phase 3 — One vocabulary for the queue tables

`squid/persistence/queue.py` already shares the claim protocol. What is not shared is the column
vocabulary and what the counters mean:

| Inconsistency | Tables |
|---|---|
| `locked_at` instead of `claimed_at` | `record_recompute_queue`, `search_projection_queue`, `search_embedding_queue` |
| `attempts` incremented on **claim**, not on failure | `notification_deliveries` (`squid/notifications/infrastructure/repository.py`, the claim `UPDATE`) |
| Extra `claim_count`, written but never read | `domain_event_deliveries` |
| No `dead_at` | `record_recompute_queue` |

**Decision:** `attempts` means "failures recorded" on every queue.

- Rename the three `locked_at` columns (and their partial-index predicates) to `claimed_at`.
- Make `notification_deliveries` increment `attempts` in `fail_delivery` instead of in the claim
  `UPDATE`. The first failure still sees `attempts = 1`, so `retry_delay` and the eight-attempt cap
  behave exactly as today. The one change is that a worker crash (lease expiry, then reclaim) no
  longer uses up an attempt, which matches every other queue.
- Drop `domain_event_deliveries.claim_count`, the `claim_count` field on `DomainEventDelivery`, and
  the optional `claim_count` slot in `QueueSpec`. Update `docs/plans/durable-queues.md`, which
  describes it.
- `record_recompute_queue` keeps having no `dead_at`. **Decision:** it never dead-letters, as its
  `QueueSpec` already explains (`RECORD_RECOMPUTE_QUEUE_SPEC`): abandoning a scope would leave
  published records stale indefinitely. The mixin makes `dead_at` optional for this reason.
- Extract a declarative mixin for the shared columns so the shape is stated once.

Offline application makes this a single migration.

## Phase 4 — One locking mechanism on `builds`

Three concurrency controls currently sit on one table:

- `is_locked` (boolean), maintained by the `set_locked_at` trigger
  (`squid/persistence/postgres_entities.sql`), which derives `locked_at` from it.
- `locked_at` / `lock_token` / `lock_expires_at`, a fencing lease added by `a1b2c3d4e5f6`.
- `__mapper_args__["version_id_col"] = revision`, SQLAlchemy optimistic locking.

The trigger and the lease conflict: the lease writes `locked_at` as the time the lease was taken, and
the trigger overwrites it from `is_locked` on the next update. Keep the lease, drop `is_locked` and
the trigger (update `EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS`), and leave `version_id_col` alone,
because it serves a different purpose and also drives `discord_posts.applied_revision`. `is_locked`
is still read in `squid/builds/application/locks.py`, `squid/builds/infrastructure/locks.py` and
`squid/builds/infrastructure/repository.py`.

## Phase 5 — One representation of door and extender timings — **deferred**

`doors.normal_opening_time` / `visible_*` duplicate `door_timing_variants(build_id, label)`, which
carries the same fields plus reset times; `extenders` and `extender_timing_variants` mirror this.
The records repository reads the variant tables and falls back to the columns (`_door_timings`,
`_extender_timings`, `_extender_candidate` in `squid/records/infrastructure/repository.py`), but
**nothing writes a variant row**. The door fallback is therefore always taken, and
`_extender_candidate` receives an empty tuple, so extender fastest records can never resolve.

**Decision:** deferred until the extender submission work in TODO.md
(`Add piston-extender create/update submission persistence`) settles. That work decides whether
builds need several timing variants. If they do, write the variant rows first and then drop the
columns; if not, drop the variant tables and the fallback. The extender records bug above exists
today independently of this cleanup and is tracked in BUGS.md.

## Phase 6 — Retire the record denormalizations

Both are application changes.

- **`record_definitions` duplicates five columns from `record_competitions`** (`record_class`,
  `build_kind`, `version_scope`, `version_id`, `category_key`), and nothing keeps them in sync. A
  composite FK cannot enforce it (see the corrections above). Drop the five columns and read them
  through `competition_id`, which means reworking the reads in
  `squid/records/infrastructure/repository.py` and moving the indexes that use them.
- **`builds.record_category`** is a legacy denormalization superseded by `record_definitions` /
  `record_results` / `record_result_holders`. The remaining references are the column, its CHECK
  and index in `squid/builds/models.py`, the field in `squid/builds/domain/models.py`, and
  `squid/submissions/infrastructure/inference_payloads.py`. The `"record_category"` strings in
  `squid/suggestions` and `squid/records/errors.py` are unrelated labels.

## Not planned

`verification_codes.id` running out at 32,767 is tracked in BUGS.md. The UUIDv7 primary-key
migration and the legacy smallest-door cleanup are tracked in TODO.md.
