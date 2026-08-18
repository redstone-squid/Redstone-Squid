# Schema integrity hardening

> **Status.** Preliminary — three defects are verified in-tree below; everything else in the
> source review is an unverified claim that needs design work before it becomes a plan. Do not
> implement from this document yet. Re-verified 2026-08-18 against HEAD `f4cd124b` (the branch was
> rebased after the original spot-check against `c490a0da`, rewriting every commit hash; all three
> defects are unchanged in content and line number). `record_recompute_queue`'s fencing bug is
> tracked separately in [`durable-queues.md`](durable-queues.md) (already verified there, design
> agreed) and is not duplicated here.

## Context

A review of the current schema (models under `squid/*/infrastructure/models.py`, functions under
`schema/structure/public/functions/`, and the generated dumps `schema_dump.sql` /
`squid/persistence/postgres_entities.sql`) surfaced a long list of claimed defects, ranging from
"will corrupt data today" to "would be nice for a 9.5/10 schema." This document exists to separate
those two things. Three claims were spot-checked against the actual files on
`schematics-phase-0` (originally HEAD `c490a0da`, re-verified 2026-08-18 at HEAD `f4cd124b`) and
are real. The rest have *not* been verified — they are recorded here as a punch list for
follow-up review, not as accepted findings.

## Verified defects

### 1. `delete_orphaned_build_vote_sessions_after_builds_delete` deletes unrelated vote sessions

`schema/structure/public/functions/delete_orphaned_build_vote_sessions_after_builds_delete.sql:6`:

```sql
SELECT vote_sessions.id
FROM vote_sessions vs
LEFT JOIN build_vote_sessions bvs ON vs.id = bvs.vote_session_id
LEFT JOIN delete_log_vote_sessions dvs ON vs.id = dvs.vote_session_id
WHERE bvs.vote_session_id IS NULL AND dvs.vote_session_id IS NULL
```

The inner table is aliased `vs`, so the unqualified `vote_sessions.id` in the `SELECT` list does
not resolve to the aliased row — it resolves to the outer `DELETE FROM vote_sessions` target,
making the subquery return every vote session's id whenever at least one orphan exists. It also
never joins `generic_vote_sessions`, so any generic poll looks orphaned unconditionally. Confirmed
by reading the function body; not yet reproduced with an integration test.

**Needs a design decision, not just a fix**: the review proposes replacing the build-level
`AFTER DELETE` trigger with a narrower `AFTER DELETE` trigger on `build_vote_sessions` that only
deletes `OLD.vote_session_id` when no other subtype row references it. That changes trigger
ownership (currently attached to `builds`, would move to `build_vote_sessions`) and needs to be
checked against how `delete_log_vote_sessions` and any future subtype tables are supposed to
participate — worth deciding explicitly rather than patching the join in place, since the
join-alias mistake is exactly the kind of thing that recurs if the query keeps enumerating subtype
tables by hand.

### 2. `check_record_category` accepts arbitrary strings

`squid/builds/infrastructure/models.py:50` / `schema/structure/public/tables/builds/table.sql:6`:

```sql
record_category = ANY (ARRAY['Smallest', 'Fastest', 'First', 'Smallest Fastest', 'Fastest Smallest', NULL])
```

For any value not in the list, every element comparison is `false` except the comparison against
`NULL`, which is `unknown`; `false OR unknown` is `unknown`, and Postgres treats a `CHECK` that
evaluates to `unknown` as satisfied. The constraint therefore accepts any string, not just the
five listed values. Confirmed by reading the constraint text in both the model and the generated
schema.

### 3. `vote_sessions` threshold `CHECK` has the same null hole

`squid/voting/infrastructure/models.py:34`:

```sql
CASE WHEN kind = 'generic'
  THEN pass_threshold IS NULL AND fail_threshold IS NULL
  ELSE pass_threshold > 0 AND fail_threshold < 0
END
```

For a non-generic session with both thresholds `NULL`, the `ELSE` branch evaluates to `unknown`,
which satisfies the constraint — so a build/delete-log vote session can be created with no
thresholds at all, defeating the apparent intent. Confirmed by reading the column definitions
(`pass_threshold`/`fail_threshold` are nullable `SmallInteger`) alongside the constraint text.

Both (2) and (3) are the same class of bug: a `CHECK` built from a comparison against a nullable
column, without an explicit `IS NOT NULL`, silently degrades to "accept anything." That suggests
an audit pass — grep every `CHECK`/`CheckConstraint` touching a nullable column — rather than
fixing these two in isolation. Not yet done.

## Unverified — needs scoping before it becomes a plan

The source review also raised the following, none of which have been checked against the current
code in this pass. Each needs an owner to (a) confirm the claim is still true against HEAD, and
(b) make a design call, since several are genuine trade-offs rather than obvious bugs:

- **Identity columns on FK-only columns** (`build_edit_history.build_id`,
  `build_vote_sessions.vote_session_id`, `delete_log_vote_sessions.vote_session_id`,
  `votes.vote_session_id`) — plus whether `build_edit_history` (PK on `build_id` with a redundant
  `UNIQUE(build_id, version)`, and a `smallint` version next to `builds.revision bigint`) is meant
  to be true history or should be dropped as dead.
- **`verification_codes.id` as `smallint`** and whether the table's constraints match its access
  pattern (unique code, uniqueness of the "one valid Java identity" invariant in
  `account_identities`).
- **`votes` not FK-referencing `vote_session_options`** — whether option identity should become a
  real surrogate key with FK-backed votes, and what a `status`/`result` state machine + deadline
  ordering constraint should look like.
- **Joined-table inheritance not enforced by Postgres** for `builds`/subtype tables and
  `vote_sessions`/subtype tables — whether deferrable constraint triggers are worth the complexity
  versus relying on the ORM/application layer.
- **`record_definitions` duplicating identity fields from `record_competitions`**, plus
  `RecordDefinition.competition_id`'s random default and `record_definition_facets.facet_id` vs.
  bigint tag ids.
- **Permission model cross-row invariants** (role-inclusion cycles, cross-guild role inclusion,
  audit-log append-only enforcement, the epoch-trigger fail-open behavior on a missing singleton
  row) — this one in particular is a security-relevant design decision, not a mechanical fix.
- **Schematics analyzer-output constraints** (dimension/bounding-volume checks, publication state
  machine coherence, `uploaded_by_discord_id` vs. an account FK, job/queue status design parity
  with the (soon to be redesigned, per `durable-queues.md`) queue pattern).
- **Tag/unit model** (alias uniqueness scope, canonical-name/alias collisions across tables,
  unit-dimension compatibility, tag-relation symmetry) — likely the largest single redesign in the
  list if pursued.
- **Draft ownership duplicated** between `submission_drafts.owner_account_id` and
  `submission_draft_access` — pick one source of truth.
- **`messages` vs. `starboard_origin_messages`** storing overlapping facts about the same Discord
  message.
- **Performance/maintainability items**: stale `schema_dump.sql` (91 models vs. 64 dumped tables —
  worth confirming the count, then deciding whether to regenerate automatically or delete the
  dumps), over-broad `AFTER UPDATE` triggers on `builds` re-enqueueing work on unrelated column
  changes, host-clock-based queue claim timestamps vs. DB-time claim tokens, missing indexes on
  FK columns used for reverse lookups, float vs. fixed-precision for voting/starboard scores,
  and a uniqueness/check pass on `versions`.

## Next step

Before turning any of the unverified items into a plan: re-confirm each claim against current
HEAD (several may already be stale, the way the review itself notes some earlier findings were
doc bugs that got fixed upstream), then bring the security-relevant ones (permission model,
schematics publication state) to a design discussion rather than treating them as mechanical
fixes. The three verified defects above are safe to fix independently and don't need to wait on
that scoping work.
