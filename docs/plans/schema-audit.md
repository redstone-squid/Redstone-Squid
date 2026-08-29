# Close the remaining schema-audit findings

> **Status.** Phase 0 (ten findings) is landed and content-verified in-tree 2026-08-18 — see the
> updated commit table below; the branch was rebased after the original landing, which rewrote
> every commit hash in this document without changing any of the underlying fixes. The nine
> findings in Phases 1-6 are still fully open: none of the described schema or application changes
> have started. The "correctness gate" baseline (13 `modify_comment` diffs) and the "Not planned"
> items are unchanged. Re-verified item-by-item against current code, not assumed from the original
> audit.

## Context

A full audit of the 101 application tables and their SQLAlchemy models — reading the metadata
rendered to DDL and cross-checking it against `schema_dump.sql` and the model sources — turned up
nineteen findings, ranging from foreign keys minted from sequences to four architectural
duplications that need application changes to unwind.

Ten landed directly (see Phase 0). The rest are recorded here because each needs either a decision,
an application-layer change, or both, and because two of the original findings turned out to be
wrong once implementation started — that correction is worth keeping.

**Operating assumptions for everything below**, confirmed with the maintainer:

- **Migrations are applied offline.** Workers are stopped, so no expand/contract sequencing is
  required and a plain `CREATE INDEX`/`ALTER TABLE` is preferable to the `CONCURRENTLY` variants.
- **Existing data is not precious.** Backfills may be lossy and constraints may be applied directly
  rather than `NOT VALID` then validated.

**The correctness gate is `test_migrations_create_schema_without_drift`**
(`tests/integration/test_alembic_migrations.py`). It runs the whole chain against a clean
PostgreSQL container and then `alembic check`. It is *already failing* at baseline with **13
`modify_comment` diffs** — model attribute docstrings that were extended without matching migration
column comments, in `api_keys`, `discord_sync_queue`, `idempotency_requests`,
`record_recompute_queue`, `schematic_jobs`, `schematic_render_queue` and the two `search_*_queue`
tables. Any phase below is verified by confirming the diff list still contains exactly those 13 and
nothing structural. Fixing the 13 is its own small task, unclaimed.

Watch for a trap when adding model documentation: `Base.__init_subclass__`
(`squid/persistence/base.py:16-44`) turns an attribute docstring into a column comment, so prose
added to a `mapped_column` is a schema change. Use a `#` comment for notes about the mapping.

## Phase 0 — Landed — **DONE**

Commit hashes below are current as of 2026-08-18; the branch was rebased after these landed,
which invalidated the hashes originally recorded here (`424e644a`, `cc072ba2`, `eb8206bb`,
`70aebd41`, `daec045c`, `e00bf658`, `bcac687a` — none resolve any more). Content re-verified
unchanged.

| Commit | Finding |
|---|---|
| `5061234e` | `Identity()` on three foreign-key columns in `votes`, `build_vote_sessions`, `delete_log_vote_sessions` |
| `e8dbbada` | `build_edit_history` — dropped outright rather than fixed in place (single-row-per-build primary key, redundant unique, identity on the FK, no writer; nothing ever wrote to it) |
| `9c12b7b4` | `tag_definitions_numeric_metadata_check` admitted every row |
| `4eaaa220` | `messages.id` and `server_settings.server_id` declared `SERIAL` for externally assigned snowflakes (model-only fix, `autoincrement=False`; no migration needed since the deployed baseline never had the sequence) |
| `ac4f91a2` | 37 foreign-key indexes, chiefly the 22-constraint `accounts` erasure fan-out, plus `passive_deletes=True` on the four `Build` collections (both original findings landed together) |
| `53877d09` | `api_keys.created_by_account_id` ON DELETE; `starboards.colour` width; three nullable `created_at` columns |

Migration chain (unaffected by the rebase — Alembic revision ids are independent of git commit
hashes): `c4e8f2a1b6d3` → `d5f9a2c7b481` → `e6a0b3d8c592` → `f7b1c4e9d6a3` → `a8c2d5f0e7b4` →
`b9d3e6a1f8c5`.

One stray leftover from the `build_edit_history` drop: `schema/structure/public/tables/build_edit_history/`
(`table.sql`, `policies.sql`) is still tracked in git even though the table no longer exists in any
model or migration — a stale dump, not a schema defect. Worth a cleanup commit but out of scope
here; see `schema-integrity-hardening.md`'s note on `schema_dump.sql` staleness.

## Two corrections to the original audit

Recorded because both were reported as easy fixes and neither is.

**`votes.option_id` cannot take a foreign key on its own.** The audit called it a dangling
reference and proposed a unique constraint on `vote_session_options.identifier` plus an FK. That
does not work. `_close_at_threshold` (`squid/voting/infrastructure/repository.py:329-341`) joins
options with `or_(VoteSessionOption.guild_id == Vote.guild_id, VoteSessionOption.guild_id == 0)`,
`_load` repeats the fallback at line 505, and `VoteSessionSnapshot.options_for_guild`
(`squid/voting/domain/models.py:279-283`) encodes the same rule. A vote cast in guild *G*
legitimately resolves to an option row stored under guild `0`. So:

- `(vote_session_id, guild_id, option_id)` → rejects valid votes.
- `(vote_session_id, option_id)` → cannot be unique, because the fallback requires the guild-`G`
  and guild-`0` rows to coexist with the same identifier.

The dangling reference and the `0` sentinel are the same defect. Phase 2 fixes both or neither.

**The `record_definitions` → `record_competitions` composite foreign key is half-vacuous.**
`record_definitions.version_id` is NULL for `all_time` scope, and under `MATCH SIMPLE` a NULL in any
referencing column satisfies the constraint trivially — so it would enforce nothing for exactly the
rows most likely to drift. `MATCH FULL` is worse: it would reject them outright. See Phase 6 for
the fix that actually holds.

## Phase 1 — Mechanical schema corrections

Independent of each other; any can go first.

- **Name every constraint from one convention.** `Base.metadata` has no `naming_convention`
  (`squid/persistence/base.py`), so 22 constraints are unnamed and carry PostgreSQL-invented names
  while everything else is named by hand: all of `starboard_*`, `guild_vote_emojis` (×3),
  `guild_vote_role_weights` (×2), `generic_vote_sessions` (×2), `domain_event_deliveries` (×2), and
  the three `server_settings` uniques. A convention matching PostgreSQL's own defaults
  (`%(table_name)s_%(column_0_name)s_fkey` / `_key`) reproduces the existing names for every
  single-column FK and unique, so those need no rename; the three unnamed `CheckConstraint`s do need
  explicit names. Verify empirically against the drift test rather than by reasoning — if the
  generated names diverge, a rename migration is needed.

- **Constrain `builds.category`.** It is the `polymorphic_on` discriminator
  (`squid/builds/infrastructure/models.py`), yet it is nullable, has no CHECK, and `Build` declares
  no base `polymorphic_identity`. A row with `category IS NULL` or an unrecognised value cannot be
  loaded at all. Add `CHECK (category IN ('Door','Extender','Utility','Entrance','Other'))` per
  `BuildCategory` (`squid/builds/domain/models.py:102-109`) and `NOT NULL`. Backfill decision
  needed: NULL rows map to `'Other'`, which also wants a matching `other_builds` row to keep the
  joined-table inheritance consistent.

- **Guard `permission_role_includes` against cycles in the database.** `_would_cycle`
  (`squid/permissions/application/administration.py:516-527`, called from `:497`) is a
  read-then-write with no lock, so two concurrent `add_include` calls that are each individually
  acyclic can commit a cycle. The DB only has `role_id <> included_role_id`. Still true as of
  2026-08-18 — no trigger added. A `CONSTRAINT TRIGGER` running a recursive CTE belongs in
  `squid/persistence/postgres_entities.sql` — note the arity assertion at
  `squid/persistence/alembic_entities.py:15-16` (`EXPECTED_FUNCTIONS = 12`, `EXPECTED_TRIGGERS = 38`)
  counts functions and triggers and must be updated.

- **Object-key integrity in media and schematics.** `schematic_files.object_key` is not unique, and
  `media_artifacts.object_key` has an index but no FK to `media_artifact_objects.object_key`.
  **Open question before writing either:** whether an artifact row is ever written before its object
  row, and whether objects are tracked only for published artifacts. If the write ordering is
  artifact-then-object, the FK cannot be added without reordering the media normalization worker.

## Phase 2 — Retire the `0` sentinel in guild scoping

Unblocks the `votes.option_id` foreign key, so it pays for itself twice.

`votes.guild_id`, `vote_session_options.guild_id` and `starboard_sources.channel_id` all default to
`0` standing in for "no guild" / "whole guild", and none carries a foreign key. The schema already
uses `UNIQUE NULLS NOT DISTINCT` in six places, so real NULLs work and would let the missing foreign
keys be added.

Work: change the three columns to nullable with no default; migrate `0` → NULL; rewrite the
guild-`0` fallbacks in `squid/voting/infrastructure/repository.py` (lines 335 and 505) and
`VoteSessionSnapshot.options_for_guild` to use `IS NULL`; then add the unique constraint on
`(vote_session_id, guild_id, identifier)` and the `votes.option_id` foreign key that follows from it.

## Phase 3 — One vocabulary for the queue tables

`squid/persistence/queue.py` already shares the claim protocol well — the docstring explains why
acknowledgement is deliberately left to vary. What is not shared is the column vocabulary:

| Column | Tables |
|---|---|
| `claimed_at` | `discord_sync_queue`, `schematic_jobs`, `schematic_render_queue`, `media_normalization_jobs`, `notification_deliveries`, `domain_event_deliveries`, `submission_finalization_jobs` |
| `locked_at` | `record_recompute_queue`, `search_projection_queue`, `search_embedding_queue` |

`domain_event_deliveries` additionally carries both `claim_count` and `attempts`, and
`record_recompute_queue` is the only queue with no `dead_at`.

Work: rename the three `locked_at` columns to `claimed_at`, reconcile the duplicate counter, and
extract a declarative mixin so the shape is stated once. The rename touches `queue.py` and every
adapter; offline application makes it a single migration.

## Phase 4 — One locking mechanism on `builds`

Three concurrency controls currently sit on one table:

- `is_locked` (boolean), maintained by the `set_locked_at` trigger
  (`squid/persistence/postgres_entities.sql:67-78`), which derives `locked_at` from it and
  overwrites whatever the lease logic wrote.
- `locked_at` / `lock_token` / `lock_expires_at`, a fencing lease added by `a1b2c3d4e5f6`.
- `__mapper_args__["version_id_col"] = revision`, SQLAlchemy optimistic locking.

The trigger and the lease actively fight: the lease writes `locked_at` as the moment the lease was
taken, and the trigger rewrites it from `is_locked` on the next update. Keep the lease, drop
`is_locked` and the trigger, and leave `version_id_col` alone — it serves a different purpose and
also drives `discord_posts.applied_revision`.

## Phase 5 — One representation of door and extender timings

`doors.normal_opening_time` / `visible_*` duplicate `door_timing_variants(build_id, label)`, which
carries the same fields plus reset times; `extenders` and `extender_timing_variants` mirror this.
The records repository reads the variants table and falls back to the columns
(`squid/records/infrastructure/repository.py:775-798`), but **nothing writes a variant row**, so the
fallback is always taken for doors and `_extender_candidate` receives an empty tuple — meaning
extender fastest records can never resolve.

Still true as of 2026-08-18: no code writes a `DoorTimingVariant`/`ExtenderTimingVariant` row
anywhere. **The "TODO.md already tracks this" claim is now stale** — TODO.md's current extender
entry (`- [ ] Add piston-extender create/update submission persistence`, line 19) is about
submission persistence for extenders generally, not the timing-variant writer specifically; it may
or may not cover this when done. Sequence the variant writer first regardless; collapsing to one
representation only makes sense once something populates it.

## Phase 6 — Retire the record denormalizations

Two related pieces, both application changes:

- **`record_definitions` duplicates five columns from `record_competitions`** (`record_class`,
  `build_kind`, `version_scope`, `version_id`, `category_key`) with nothing keeping them in sync,
  and per the correction above a composite FK cannot enforce it. The fix that holds is dropping the
  five columns and reading them through `competition_id`, which means reworking the reads in
  `squid/records/infrastructure/repository.py`.
- **`builds.record_category`** is a legacy denormalization superseded by the whole
  `record_definitions` / `record_results` / `record_result_holders` subsystem. 14 call sites remain.

## Not planned

`verification_codes.id` exhausting at 32,767 is recorded in BUGS.md with the correct diagnosis. The
UUIDv7 primary-key migration and the legacy smallest-door cleanup are in TODO.md. None is duplicated
here.
