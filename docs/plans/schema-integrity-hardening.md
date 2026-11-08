# Schema integrity hardening

> **Status (2026-11-08).** Phases 1–6 are implemented except two items, marked below:
> 1.3 (display units) waits on a decision, and Phase 4 (`messages`) is unblocked now that
> `completed/discord-state-reconciliation.md` Phase 3 is done. The same operating assumptions as `docs/plans/completed/schema-audit.md`
> apply: migrations run offline, existing data is not precious, and foreign keys are immediate
> unless the write order cannot be fixed. Every phase must keep
> `test_migrations_create_schema_without_drift` passing.
>
> | Phase | Implementation |
> |---|---|
> | 1.1, 1.2 | `d1a4f7c2e9b5` |
> | 1.4 | Starboard cog bulk-delete handler |
> | 2 | `e2b5a8d3f0c6` |
> | 3 | `f3c6b9e4a1d7`; queue checks come from `QueueColumns` |
> | 5 | `squid/core/scores.py` (`round_score`) and `squid/persistence/types.py` (`database_now`) |
> | 6 | `scripts/export_schema.sh` and `.github/workflows/schema-artifacts.yml` |

## Context

An external review of the schema listed a long set of claimed defects, from "corrupts data today"
to "nice to have". This plan keeps only the ones that are still true at HEAD and records a decision
for each one. Several claims were already fixed, mostly by `completed/schema-audit.md`; they are listed so
nobody re-reviews them.

## Already resolved

| Claim | Resolved by |
|---|---|
| `check_record_category` accepts any string | The column was dropped (`completed/schema-audit.md` Phase 6). |
| `vote_sessions` threshold CHECK has a NULL hole | The CHECK now requires `IS NOT NULL` (`squid/voting/models.py`, `THRESHOLD_CONSTRAINT`). |
| Identity on FK-only columns; `build_edit_history` | `completed/schema-audit.md` Phase 0. |
| `votes` does not reference its option; no status or result | Phase 2 alias FK; `vote_sessions.status` and `result` have CHECKs. |
| Vote-session subtypes not enforced | `enforce_vote_session_kind_subtype` constraint triggers. |
| `title_definitions` duplicates record title fields | `completed/schema-audit.md` Phase 6. |
| Role-inclusion cycles | `check_permission_role_include_cycle` (`completed/schema-audit.md` Phase 1). |
| `schematic_uploads.uploaded_by_discord_id` | Replaced by `uploaded_by_account_id`, FK `ON DELETE SET NULL`. |
| Schematic job tables use a bespoke queue | Both use `QueueColumns`, `QueueSpec` and `ClaimedRowQueue`. |
| Missing FK indexes | Phase 0 added 37. Only `notifications.event_id` remains (Phase 3). |

## Phase 1 — Bugs that produce wrong results

### 1.1 Deleting any build deletes every vote session

`delete_orphaned_build_vote_sessions_after_builds_delete` (`squid/persistence/postgres_entities.sql`)
selects `vote_sessions.id` while the inner table is aliased `vs`. The unaliased name refers to the
outer `DELETE` target, so the subquery returns the outer row's id whenever any orphan exists. Generic
polls never have a build or delete-log row, so they always count as orphans. Reproduced against
PostgreSQL 18 on 2026-11-07: with one generic poll present, `DELETE FROM builds WHERE id = 99`, which
matches no rows, deleted every vote session (the trigger is per statement). No application code
deletes builds today, so only manual and admin deletes hit it.

**Decision:** drop the statement trigger on `builds` and its function. Add a row-level
`AFTER DELETE` trigger on `build_vote_sessions` that runs
`DELETE FROM vote_sessions WHERE id = OLD.vote_session_id AND kind = 'build'`. The subtype constraint
trigger already guarantees that a `build` session has exactly one subtype row, so no other table
needs checking, and the query no longer lists subtype tables by hand. When the delete comes from
the parent (a cascade from `vote_sessions`), the statement matches nothing. One function and one
trigger are replaced, so `EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS` stay the same. Add an integration
test: deleting a build removes only its sessions, and a no-op delete keeps generic and delete-log
sessions.

### 1.2 A guild admin can change what a global role grants

`permission_role_includes` lets a global role (`guild_id IS NULL`) include a guild role.
`PermissionAdministration.add_include` (`squid/permissions/application/administration.py`) authorizes
against the including role only and never compares the two `guild_id`s. A guild admin who edits their
own role then changes what the global role grants in every guild. Guild A including guild B's role is
not reachable through the UI, but nothing forbids it either.

**Decision:** an include is valid only when `included.guild_id IS NULL OR included.guild_id =
role.guild_id`.

- Reject other includes in `add_include` with a `ValidationError`.
- Enforce the same rule in `check_permission_role_include_cycle`, so no trigger is added and the
  counts don't change.
- The migration deletes existing edges that break the rule.

### 1.3 Display units are shown without converting the value — **waiting on a decision**

No write path sets a display unit today: no seeded tag has a default display unit, and submissions
never set one. The bug is therefore latent, and the fix touches five renderers (including the two
public-summary paths in `squid/builds`), each of which would need the unit definitions loaded.


Numeric tag values are stored in the tag's canonical unit, and `tag_units.scale_to_base` gives each
unit's factor. All three renderers print the stored value next to the display unit's key without
converting it:

- `_render_tag_assignment` in `squid/search/infrastructure/projection.py`
- `RecordFacet.category_name` in `squid/records/application/models.py`
- `squid/api/v1/schemas/builds.py`

Any display unit that differs from the canonical unit therefore shows a wrong number.

**Decision:** keep display units and convert.

- Add one helper in `squid/tags` that converts a canonical value into a display unit
  (`value * canonical.scale_to_base / display.scale_to_base`), and use it in all three renderers.
- Display units are written only by seeds and by build tag assignments
  (`squid/builds/infrastructure/repository.py`, where assignments are built). On that path, drop a
  display unit whose `dimension` differs from the canonical unit's instead of failing the
  submission.
- Add no database trigger: units are seed data and the one application write path is guarded.

### 1.4 The starboard ignores bulk deletes

`MessageFactCog` handles `on_raw_bulk_message_delete` (`squid/bot/messages.py`), but the starboard cog
only handles `on_raw_message_delete` (`squid/bot/starboard/cog.py`). A channel purge leaves the
starboard's origin rows looking alive. **Decision:** add the bulk handler to the starboard cog,
reusing the single-delete logic. Phase 4 later removes the duplicated deletion state altogether.

## Phase 2 — Build triggers fire on bookkeeping writes

`builds_enqueue_search` and `builds_enqueue_discord_sync` fire on every `UPDATE` of `builds`. The lease
writes in `squid/builds/infrastructure/locks.py` (acquire, release, stale cleanup) and the embedding
write in `squid/builds/infrastructure/embeddings.py` change nothing visible, yet each one:

- re-enqueues search projection (cheap, because the projection hash stops re-embedding);
- for doors and extenders, enqueues a recompute of every record of that kind (expensive);
- enqueues a Discord sync of the build and its vote cards. This is a real message edit, because
  sync is generation-based.

**Decision:** add a `WHEN` clause to both `builds` triggers that ignores those columns, written as an
exclusion so new columns count as content by default:

```sql
WHEN ((to_jsonb(OLD) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])
      IS DISTINCT FROM (to_jsonb(NEW) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']))
```

`WHEN` cannot be combined with `INSERT OR DELETE` in a way that reads `OLD` on insert, so split each
into an `AFTER INSERT OR DELETE` trigger and an `AFTER UPDATE ... WHEN` trigger that share the same
function. That adds two triggers to `EXPECTED_TRIGGERS`. Test: acquiring and releasing a lease
enqueues nothing.

## Phase 3 — Missing constraints

One migration. Every item is independent.

- **Build subtypes.** Nothing enforces "exactly one subtype row matching `builds.category`" across
  `doors`, `extenders`, `utilities`, `entrances` and `other_builds`. **Decision:** add
  `enforce_build_category_subtype`, a deferred constraint trigger modeled on
  `enforce_vote_session_kind_subtype`, on `builds` and the five subtype tables. Deferral is required
  here, because the ORM inserts the parent before the subtype row. This adds one function and six
  triggers.
- **Schematic analyzer output** (`build_schematics`, `squid/schematics/models.py`):
  - `>= 0` on every dimension, `allocated_*`, count and size column;
  - `bounding_volume = width::bigint * height * length`;
  - `block_count <= bounding_volume`;
  - each tight dimension `<=` its allocated dimension.

  The decoder already rejects negatives, so these only back it up.
- **Schematic publication.** Add `visibility <> 'public_download' OR published_at IS NOT NULL`,
  `withdrawn_at IS NULL OR published_at IS NOT NULL` and `withdrawn_at >= published_at`. The app
  already treats a row as downloadable only when published and not withdrawn, so this closes no live
  hole.
- **Schematic queues.** `schematic_jobs` and `schematic_render_queue` lack the
  `(claimed_at IS NULL) = (claim_token IS NULL)` and `attempts >= 0` checks the other queues have.
  Move both checks into `QueueColumns` so every queue gets them from one place.
- **`versions`.** Add `UNIQUE (edition, major_version, minor_version, patch_number)`,
  `CHECK (edition IN ('Java', 'Bedrock'))`, and `>= 0` on the three numbers. `seed.sql` has no
  duplicates.
- **`title_definition_facets`.** `facet_id` is `Integer` with no FK, while tag ids are `bigint`, and
  only `restriction` facets are ever written. Widen `facet_id` to `bigint`, add an FK to
  `tag_definitions.id` with `ON DELETE CASCADE`, and narrow the `facet_kind` CHECK to
  `'restriction'`. `FacetKind` keeps its other members, which candidate facets still use.
- **`TitleDefinition.title_id`** has `default_factory=uuid.uuid4`, which would invent an identity
  if a caller forgot it. Remove the default (model only, no DDL).
- **`notifications.event_id`** has no index, but the `domain_events` prune does a `NOT EXISTS` against
  it and cascades into it. Add the index.
- **`verification_codes`.**
  - Widen `id` to `bigint`.
  - Every lookup matches the code's HMAC alone (`squid/accounts/infrastructure/repository.py`), so
    two valid rows with the same code would be ambiguous. Add `UNIQUE (code) WHERE valid`, and make
    issuing a code retry with a fresh one on a unique violation.
  - In `replace_verification_code`, delete the UUID's earlier rows instead of flipping `valid`.
    This keeps one row per UUID, so the table stops growing.
  - This closes both `verification_codes` entries in BUGS.md; remove them when it lands.

## Phase 4 — One source of truth

- **`messages` vs `starboard_origin_messages`** — **not started; reconciliation Phase 3 is done, so it can start.** Both store the snowflake, guild, channel, author,
  creation time and `deleted_at` for the same Discord message, and nothing links them. They already
  diverge: the starboard upsert resets `deleted_at` to NULL, and see 1.4 for bulk deletes.
  **Decision:**
  - Add an FK `starboard_origin_messages.id` → `messages.id`, and keep only the starboard-specific
    columns (`author_is_bot`, `is_nsfw`, `has_image`, `seen_at`). Deletion state is then read from
    `messages`.
  - The starboard reaction path gets or creates the `messages` row in the same transaction, the
    ensure-on-write rule from `docs/plans/completed/discord-state-reconciliation.md`.
  - That plan's Phase 3 changed the same write path: `record_votes` and `recount_votes` now also
    call `ensure_server_settings`.

## Phase 5 — Float scores and the database clock

**Float scores.** Role multipliers are typed in freely by admins (`squid/bot/settings_view.py` parses
them with `float()`), so a weight of 0.1 is allowed. Ten such votes sum to 0.9999…, which misses a
threshold of 1 (`net` against `pass_threshold` in `squid/voting/infrastructure/repository.py`). The
starboard compares `last_rendered_score != score`, and a summation order that changes between runs
can trigger a spurious re-render. **Decision:** round weighted sums to 6 decimal places wherever they
are computed: the vote threshold query, the starboard score, and the threshold checks in
`squid/starboard/domain/models.py`. Keep the float columns.

**Database clock.**

`squid/persistence/queue.py` uses `func.now()` throughout, but a few paths mix in Python's
`Instant.now()` and compare it with database time:

- the media normalization claim and heartbeat (`squid/media/infrastructure/repository.py`);
- the schematic cleanup claims (`squid/schematics/infrastructure/jobs.py`);
- artifact cleanup and `_publication_expiry()` (`artifact_publication.py`), where `last_seen_at`
  (database time) is compared with `cleanup_started_at` (host time).

Leases are minutes long, so normal clock skew is harmless, but the comparisons are not meaningful
across hosts. **Decision:** switch these to `func.now()`.

## Phase 6 — One schema source, two views

`schema_dump.sql` and `schema/structure/` serve different readers: the dump is one file to grep or
paste into a review, and the tree has one file per table and function, which is easier to browse and
gives smaller PR diffs. Both stay. They used to have different sources: the dump came from a database
built with `alembic upgrade head`, while the tree was pgstrap output from June 2025 that nothing
regenerated, and `just generate-schema` dumped production.

**Implemented:** both views come from one throwaway PostgreSQL container, built by
`scripts/export_schema.sh`. CI and `just schema-docs` run that same script, and the workflow fails a PR
when either output differs from the migrations.

- **Splitter.** Our fork of [pg_split_dump](https://github.com/Glinte/pg_split_dump), downloaded from a
  release pinned by tag and SHA-256 in the script. pgstrap was dropped: its splitter,
  `pg-schema-dump`, crashes on the `avg` and `sum` aggregates that pgvector installs in `public`
  (`pg_get_functiondef` rejects aggregates), and it is unmaintained.
- **Fork changes.** `--no-owner`, `--pg-dump-arg` and `--fk-in-table`. The first two are on the
  `pg-dump-extra-args` branch, intended for upstream. Folding foreign keys into their table files means
  `index.sql` can no longer be loaded as is, which is fine because the tree is for reading; the dump
  and the migrations build databases.
- **Removed.** `package.json`, `package-lock.json`, `pgstrap.config.js` and the `generate-schema*`
  recipes. Node is no longer used anywhere in the repository.

## Not changing

- **`submission_draft_access` and `tag_relations`.** Neither has readers yet because shared
  draft editing and tag relations are not implemented, not because the tables are dead.

- **Audit log append-only.** The app role owns the table, so revoking privileges does nothing, and
  account merge legitimately rewrites `actor_account_id`. The docstring stays the contract.
- **Missing permission epoch row.** The trigger skips the bump when the row is missing, but the cache
  TTL caps staleness at 30 seconds, and the row is seeded by the migration and can only go missing
  through a manual delete.
- **One Java identity per account.** This is checked at link time under a row lock. It is deliberately
  not a database invariant, because a merge can leave two Java identities on one account.
- **Tag name and alias collisions.** Lookups resolve a name only when exactly one tag matches, so a
  collision makes the name unresolvable rather than wrong.
- **`starboard_sources.channel_id = 0`.** See `completed/schema-audit.md` Phase 2.
