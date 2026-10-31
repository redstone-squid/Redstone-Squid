# Durable work queues

> **Status.** PR 1 implemented 2026-08-16 (commits `80657335`..`eb041c00`; re-verified in-tree
> 2026-08-18 after a rebase changed these hashes, content unchanged). PR 2 —
> `enforce_queue_claim_tokens` — is still outstanding: `alembic/versions/2026_08_16_1200-c3d4e5f6a7b2_add_queue_claim_tokens.py`
> is revision 1 (nullable `claim_token`, `available_at`, no CHECK constraints), the migration
> chain past it (through head `b9d3e6a1f8c5`) contains no `enforce_queue_claim_tokens` revision
> and no new `*_claim_complete` CHECK constraint, and `squid/persistence/queue.py:302-313`
> (`token_of`) still treats a null token as a valid deploy-window state. PR 2 must not merge until
> PR 1 is deployed everywhere. The findings below were verified in-tree, not hypothetical: defects
> 1-5 and 7 were live, and both bugs in the `record_recompute_queue` audit were active
> data-correctness faults. **Defect 6 was fixed in flight by revision `c2d3e4f5a6b1`** and is
> retained below as the worked example of the hazard, not as outstanding work.
>
> **What building it proved wrong** — see [Corrections](#corrections-from-implementation) for
> detail:
> 1. The branch had **two Alembic heads**, so `alembic upgrade head` was ambiguous and every test
>    that migrates a database errored. Revision 1 merges them.
> 2. The column-comment mechanism this document relies on **does not work**, and never has.
> 3. There are **thirteen** enqueue sites, not nine.
> 4. Commits 2 and 3 cannot be separated.
> 5. `retry_delay` had a latent `OverflowError`.

## Context

Seven tables in this codebase are durable work queues. They all do the same three things: claim a
bounded batch of ready rows without two workers taking the same row, hold that claim long enough to
survive a process death, and then acknowledge, retry with backoff, or dead-letter. That protocol is
subtle, and getting it wrong costs duplicated side effects or silently dropped work.

`squid/persistence/queue.py` exists to define it once. It does not succeed, because it is factored
along the wrong seam: **it owns the acknowledgement half and leaves the claim half to each caller.**
All four adapters that use it hand-write the same

```python
select(...).where(ready_at <= func.now(), self._queue.reclaimable())
           .order_by(ready_at, pk).limit(limit).with_for_update(skip_locked=True)
```

and then call `stamp()`. The select is the subtle part. It is the part that was not shared.

The consequences compound. Because the shared piece is too small to be worth using, two of the four
adapters partially opted out and re-implemented the fence by hand; two more queues opted out
entirely and lost fencing and backoff along with it; and the reclaim predicate is now written
**eight times**, seven of them as raw SQL in one f-string.

### Verified defects

1. **`stamp()` commits a session it does not own** (`squid/persistence/queue.py:75-81`). The caller
   opens the session and holds `FOR UPDATE` row locks; `stamp` commits and silently drops them.
   Callers then read `row.id`/`row.attempts` *after* that commit, which works only because
   `expire_on_commit=False` is hardcoded in `squid/persistence/engine.py:36`. Flipping that flag
   breaks all four adapters with `MissingGreenlet`. This is also why the search projector could not
   adopt the helper — see the audit below.
2. **The fence token is the worker's clock.** `stamp()` mints `Instant.now()` in the worker process,
   but `reclaimable()` (`queue.py:37-39`) compares `claimed_at < func.now() - VISIBILITY_TIMEOUT`
   against the *Postgres* clock. Skew between them silently widens or collapses the visibility
   window, and nothing detects it.
3. **One token for the entire batch.** Every row in a `claim(limit=20)` gets the identical `Instant`,
   truncated to microseconds by `InstantUTC` (`squid/persistence/types.py:33-36`). `SKIP LOCKED`
   makes a collision improbable, not impossible.
4. **Half-parameterized.** `claimed_at` and `ready_at` are addressed through
   `InstrumentedAttribute.key` precisely because the tables disagree on names — but in the same
   `.values()` call, `attempts=`, `last_error=` and `dead_at=` are string literals
   (`queue.py:115-119`). The injected `dead_at` attribute is read only by `reclaimable()`, so a table
   naming it differently constructs fine and raises inside `fail()`.
5. **Lost fences vanish silently.** `complete`/`fail` return `False` when the claim was lost and
   discard the error text and the attempts increment with no log and no metric. A queue that has
   begun doing its work twice looks exactly like a healthy one.
6. **A transient failure forced re-render work.** *(Fixed by `c2d3e4f5a6b1`; kept as the worked
   example.)* `fail()`'s retry branch writes `ready_at`, which on `discord_sync_queue` *is*
   `enqueued_at` — which was watched by `discord_sync_queue_bump_generation` → increments
   `generation` → `project_discord_message_desired_state()` rewrites `messages.desired_revision` for
   every projected message of that resource. So one failed Discord call invalidated the
   message-projection fence, and the generic helper had no idea it was doing so. Revision
   `c2d3e4f5a6b1` draws `generation` from `discord_sync_generation_seq` inside `enqueue_discord_sync`
   and **drops both the trigger and the function**, taking `postgres_entities.sql` from 16 functions
   and 39 triggers to 15 and 38 (`squid/persistence/alembic_entities.py:20`).

   The class of hazard is what matters and it is not fixed: a column-agnostic helper writing
   `ready_at` cannot know that on one of its tables that column carries domain meaning. The same
   revision independently corroborates this — its docstring records that `ClaimedRowQueue.complete`
   deleting the acknowledged row let the per-row `generation` counter restart at 1, writing a
   revision *below* one a message had already applied and aborting the user's build edit through
   `messages_projection_revisions_valid`. Two distinct faults, both from generic queue mechanics
   colliding with per-row domain state.
7. **Under-used and over-configured.** `PostgresSchematicJobRepository` passes
   `ready_at=SchematicJob.available_at` (`squid/schematics/infrastructure/jobs.py:30`) that is never
   read, and re-implements complete/fail by hand (`jobs.py:97-157`), duplicating the exact fence and
   backoff the module exists to centralize. `PostgresSearchEmbeddingQueue` hand-rolls `complete`
   (`squid/search/infrastructure/embeddings.py:57-87`). Of the five public members, `complete` has
   two call sites and `fail` has three.

Duplication outside the module: `retry_delay` is reimplemented at
`squid/notifications/infrastructure/repository.py:892`, the visibility timeout at :888 and as
`text("interval '5 minutes'")` in `squid/search/infrastructure/projection.py:85`, and the reclaim
predicate seven more times in `squid/worker/queue_health.py:12-113`.

## What already exists and must be reused

**The strong protocol is already the majority. This work is convergence, not invention.**

`squid/events/infrastructure/repository.py:40-54` and
`squid/notifications/infrastructure/repository.py:383-395` both already claim with

```python
update(Record).where(...).values(
    claimed_at=func.now(),
    claim_token=func.gen_random_uuid(),
    attempts=Record.attempts + 1,
).returning(Record)
```

use a separate `available_at` column as the retry clock, and clear `claim_token` on release. Both
clocks and the token come from the database. `domain_event_deliveries` already carries a
`domain_event_deliveries_claim_complete` CHECK tying the two columns together. The four tables on
`ClaimedRowQueue` are the laggards; the target state is the pattern these two already demonstrate.

Two useful consequences fall out of adopting it rather than inventing something:

- `func.gen_random_uuid()` is volatile and evaluated **per row** in a multi-row `UPDATE`, so defect 3
  is fixed for free — no per-row statement needed.
- `squid/events/infrastructure/repository.py` becomes the design's falsification test. It is the
  adapter the protocol is modelled on, so it should convert with no new configuration knobs.

Also reused as-is:

- **`BackgroundTaskSupervisor`** (`squid/runtime.py:210-375`) — every queue is drained by
  `start_periodic`. No scheduling changes here.
- **`PostgresWakeListener`** (`squid/persistence/wake_listener.py`) — `LISTEN`/`NOTIFY` stays a
  latency hint over a poll that is always the durable path. Unchanged.
- **`migrated_session_factory`** (`tests/integration/conftest.py:48-88`) — runs the real Alembic
  chain, so trigger behaviour is testable. The existing queue test does not use it; see Testing.
- **`alembic_utils`** entity management (`squid/persistence/alembic_entities.py`) for the PL/pgSQL
  changes.

## Audit: are the two documented opt-outs justified?

`queue.py:9-13` argues two queues deliberately cannot use the helper. Both arguments were checked
against the code. **Neither holds, and both opt-outs are concealing bugs.**

### `SearchProjectionStore` — argument invalid, and circular

The docstring says it "runs inside a caller-owned session and hands back live ORM rows that the
projector mutates in the same unit of work." The description is accurate. The conclusion is not: the
incompatibility is *caused by defect 1*. `stamp()` commits a foreign session and `complete`/`fail`
open their own, so a helper that cannot participate in a caller's transaction excludes the one caller
that has a transaction. Its claim query (`squid/search/infrastructure/projection.py:80-91`) is
structurally identical to the other four.

Opting out cost it real safety:

- `complete()` is a bare `session.delete(item)` (:102) with **no fence**. It relies on holding the
  row lock for the whole unit of work, but the reclaim predicate at :83-86 lets a second worker take
  the row after five minutes regardless.
- `retry()` (:104-122) has **no exponential backoff**. A permanently failing projection re-runs every
  poll interval until it exhausts `PROJECTION_MAX_ATTEMPTS`.

### `record_recompute_queue` — premise true, conclusion false

The docstring says it "leases whole scopes rather than rows and acknowledges by scope, so it has no
per-row claim token to fence with." The first half is true: `claim_recompute_kinds`
(`squid/records/infrastructure/repository.py:512-527`) returns deduplicated `BuildKind`s, and ack is
set-based. The second half is false — it already stamps `locked_at` per row at :525 and could stamp a
token the same way. Two live bugs follow:

- **Work enqueued during a run is destroyed.** `enqueue` upserts on `scope_key` and resets
  `locked_at = None` (:500-508). So while worker A is recomputing `DOOR`, new `DOOR` work arrives and
  clears the lock; worker B claims it and stamps it. A then finishes and `complete_recompute` deletes
  `WHERE build_kind IN (...) AND locked_at IS NOT NULL` (:534-539) — taking B's row with it. The
  newly enqueued recomputation is silently dropped and never runs.
- **A crashed worker's lease is permanent.** `claim_recompute_kinds` filters `locked_at.is_(None)`
  (:517) with no visibility timeout at all. A row locked by a killed worker is never reclaimed, and
  `squid/worker/queue_health.py:108-109` reports it as in-flight forever rather than as a problem.

### The seam this implies

Share the **claim protocol** — readiness predicate, database-minted token, backoff and dead-letter
policy. Let the **acknowledgement shape** vary — delete the row, update it with terminal values, or
acknowledge a whole leased scope set. Split that way, scope-leasing is a third ack shape rather than
an exception, and the "it does not fit" argument dissolves.

## Design

### `squid/persistence/queue.py`

Three concepts.

**`QueueSpec`** (frozen, slots) declares one table: `name`, `model`, `key` (PK columns),
`available_at` (the retry clock, and the only column backoff ever writes), `claimed_at`,
`claim_token`, `attempts`, `last_error`; optional `enqueued_at` (defaults to `available_at`),
`dead_at` (absent where a queue must never stop retrying), `claim_count`, `pending` (extra predicate;
only `schematic_jobs` needs `completed_at IS NULL`), and `health`.

**`QueueHealthShape`** — `label`, `source`, `group_by`, `counted`. Only `domain_events` populates it,
because deliveries are counted per registered consumer through an outer join so a consumer with no
outstanding rows still reports zero. This is a four-knob escape hatch used by one of seven entries.
That is a smell, and it is still the better trade: the alternative leaves 47 lines of raw SQL
duplicating the predicate in the adapter with the strongest claim protocol and therefore the most to
lose from drift.

**`FenceOutcome`** — `applied: bool`, `dead_lettered: bool`. Returned, logged, and counted; never
discarded silently (defect 5).

**`ClaimedRowQueue(spec, session_factory=None)`**:

- `ready()`, `held_by(token)`, `held_by_any(tokens)` — predicates, so the reclaim rule exists once.
- `claim(*, limit, where=(), session=None)` — issues
  `UPDATE ... WHERE key IN (SELECT key ... FOR UPDATE SKIP LOCKED) RETURNING *`. The timestamp and
  token are minted by the database in the same statement that locks the rows, so there is no window
  in which a row is selected but unstamped and no dependence on the worker's clock (defects 2, 3).
  With `session=` it joins the caller's transaction and does **not** commit — this is what lets
  `SearchProjectionStore` in. Without, it opens and commits its own (defect 1).
- `complete(identity, token, *, values=None, session=None)` — deletes, or updates when `values` is
  given, which is the retain-terminal-row shape `schematic_jobs` needs.
- `fail(identity, token, *, attempts, error, max_attempts, terminal=False, values=None, dead_values=None, session=None)`
  — `max_attempts=None` means this queue never stops retrying, which is only correct where a
  permanently stuck row is louder than a silently dropped one.
- `complete_batch(tokens, ...)` / `fail_batch(tokens, ...)` — set-based ack for scope leases.

`retry_delay(attempts)` stays. Add `retry_delay_sql(attempts_column)`, because set-based release
covers rows with different attempt counts in one statement and so needs the policy as an expression;
a test pins the two encodings equal. The module-level `reclaimable()` is **removed** — everything
goes through the spec, so `.values()` is keyed by `InstrumentedAttribute` throughout and BasedPyright
catches a wrong column (defect 4).

Specs live beside their models — `DISCORD_SYNC_QUEUE_SPEC` in `squid/sync/infrastructure/repository.py`
and so on — never in one god-module, because `squid.persistence` must not import seven feature
packages. `squid/worker/queue_health.py` aggregates them; it is already free to import feature
adapters. All spec sites are `*.infrastructure` modules, so nothing crosses the rules in
`tests/architecture/test_boundaries.py`.

### Generated health query

Built with SQLAlchemy Core rather than an f-string, so the predicate is *literally the same Python
expression* the claim path uses. That, not the line count, is the defect:

```python
QUEUE_HEALTH_STATEMENT = union_all(*(_queue_health_select(spec) for spec in QUEUE_SPECS))
```

All seven entries generate; none needs to stay hand-written. `record_recomputation` renders
`dead_letters` as `literal(0)` via `dead_at=None`, matching today's hardcoded `0`
(`queue_health.py:110`). Its `ready`/`in_flight` counts do change meaning once it gains a visibility
timeout — a stuck lease reports as ready instead of in-flight forever. That is a metric correction
and belongs in the commit body.

### Schema

`claim_token uuid NULL` on the six laggard tables. `available_at timestamptz NOT NULL DEFAULT now()`
on the five lacking one; `schematic_jobs` and `domain_event_deliveries` already have both.

`available_at` goes on **all five**. With the generation trigger gone, the argument no longer rests on
`discord_sync_queue` at all — overloading `enqueued_at` as the retry clock is wrong on every one of
these tables on its own terms. It destroys FIFO fairness, because a repeatedly failing row keeps
jumping to the back of its own queue, and it corrupts `oldest_ready_age`, because a job that has been
failing for an hour reports as fresh. One retry clock per table, never written by the enqueue path;
`enqueued_at` keeps its single meaning of when the work was last requested.

**Deliberate non-goal:** not renaming `locked_at` → `claimed_at` on the three tables that use it. A
rename is not compatible with the rollout window below and would need its own add/dual-write/drop
dance for a cosmetic gain. `QueueSpec.claimed_at` absorbs the difference in one line per spec.

**Nine enqueue sites must also clear the token and reset the clock**, or a re-enqueued row keeps a
stale token and a stale backoff. Four PL/pgSQL functions in `squid/persistence/postgres_entities.sql`
— `enqueue_discord_sync`, `enqueue_build_search_projection`, `enqueue_metadata_search_projection`,
`enqueue_computed_record_search_projection` (located by name, not line: two of them are already being
rewritten by `c2d3e4f5a6b1`) — and five Python upserts (`squid/tags/infrastructure/repository.py:119`,
`squid/records/infrastructure/repository.py:132` and :505,
`squid/schematics/infrastructure/repository.py:256`,
`squid/search/infrastructure/projection.py:218` and :229).

No trigger work is needed here. `discord_sync_queue_bump_generation` and its function are dropped by
`c2d3e4f5a6b1`, which already moved the counts in `squid/persistence/alembic_entities.py`. The guard
is now `EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS` constants (`alembic_entities.py:14-15`), currently
`12 functions / 38 triggers` — later, unrelated function work dropped the function count further
since this paragraph was written. This change replaces four function bodies rather than adding or
removing any, so it leaves those counts alone — but rebase onto the current head before writing the
migration, because that file is under active edit.

New column attribute docstrings must be mirrored by `comment=` in the migration, because
`squid/persistence/base.py` turns them into column comments that `alembic check` compares.

### Rollout: two revisions, two releases

`deploy/compose.production.yml:14-17` runs a one-shot `migrate` service (`alembic upgrade head`)
before the long-running containers are replaced, and `DatabaseEngine.check_readiness`
(`squid/persistence/engine.py:50-60`) demands exact head equality. So there is a window in which
**old code runs against the new schema**.

Old code writes `claimed_at` and never `claim_token`, which would violate a
`(claimed_at IS NULL) = (claim_token IS NULL)` CHECK and abort the worker's drain loop. The token
column therefore ships nullable first. The window is otherwise safe in both directions: old code
fences on `claimed_at`, which still exists and is still written by new code, so a worker mid-claim
across a restart completes correctly; and a new worker reclaiming a row after the visibility timeout
writes a token, after which the old worker's `claimed_at`-fenced ack matches nothing and returns
`False` — the pre-existing correct behaviour. The one artifact is that old code's `enqueued_at`-based
backoff leaves `available_at` in the past, so a new worker retries such a row immediately. That is a
one-deploy-window latency effect, not a correctness one.

- **Revision 1 `add_queue_claim_tokens`** (this PR) — add the columns (nullable token; `available_at`
  with `server_default=now()`, metadata-only on PG11+, no table rewrite); backfill
  `available_at = enqueued_at` and then `claim_token = gen_random_uuid()` where the claim column is
  non-null; move the ready indexes from `enqueued_at` to `available_at`; `CREATE OR REPLACE` the four
  PL/pgSQL functions. Chain off the current head, which was `c2d3e4f5a6b1` when this was written —
  confirm with `alembic heads` rather than assuming, as the branch has uncommitted migration work
  landing in this same area.
- **Revision 2 `enforce_queue_claim_tokens`** (follow-up PR, merged only after revision 1 is
  deployed everywhere) — normalize any tokenless claim minted during the window, then add the six
  CHECK constraints named `<table>_claim_complete` to match the existing
  `domain_event_deliveries_claim_complete`.

An operator who prefers one release can `docker compose stop worker bot` → migrate → `up -d`. Record
that in the revision docstring as an alternative; do not depend on it.

### Duplicated helpers

Collapsed:

- `squid/notifications/infrastructure/repository.py:888` `_visibility_timeout()` and :892
  `_retry_delay()` → the shared `VISIBILITY_TIMEOUT` and `retry_delay`. The divergent
  `max(attempts - 1, 0)` clamp is **provably unreachable**: `attempts` is incremented at claim time
  (:389), so every `fail_delivery` call passes a value ≥ 1, where both formulas agree. A unit test
  pins `retry_delay(0) == 7.5s` so the divergence stays deleted on purpose rather than by accident.
- `squid/search/infrastructure/projection.py:85` `text("interval '5 minutes'")` → `ready()`.
- The seven copies in `squid/worker/queue_health.py` → one `_queue_health_select`.

Kept divergent, on purpose:

- `squid/media/infrastructure/repository.py` should adopt the protocol, but its claim also stamps
  related `media_artifact_publications` rows and it needs a `renew()` operation
  (`repository.py:235`, driven by the 30-second heartbeat in `squid/media/application/jobs.py:611-620`)
  that no other queue has. Designing `renew()` speculatively against one caller is the wrong order.
  Follow-up, revisited if a second caller appears.
- `squid/notifications/infrastructure/repository.py`'s claim and ack stay hand-rolled beyond the two
  helper deletions. Its fence includes a `generation` column and it has four distinct ack shapes
  (`complete_delivery`, `fail_delivery`, `suspend_dm`, `_cancel_pending_deliveries`). It *would* fit —
  `identity` can carry `generation` — but converting a 900-line adapter belongs in its own change.

## Commit sequence

Each commit compiles, keeps `just test` green, and leaves `alembic check` clean.

**PR 1**

1. `persistence: give the work queues a claim token and a retry clock` — models, indexes, the nine
   enqueue sites, revision 1. Nothing reads the new columns yet, so behaviour is unchanged. Body
   names the trigger chain from defect 6.
2. `persistence: rewrite ClaimedRowQueue around database-minted claim tokens` — `QueueSpec`,
   `QueueHealthShape`, `FenceOutcome`, `retry_delay_sql`, the new methods, the seven spec constants.
   The old `stamp`/`complete`/`fail` remain temporarily so nothing breaks.
3. `persistence: claim through the shared protocol and delete the timestamp fence` — converts all
   four adapters; adds `claim_token` to `SyncJob`, `ClaimedRenderJob`, `ClaimedSchematicJob`,
   `SearchEmbeddingJob`; deletes `stamp()` and the module-level `reclaimable()`. Body leads with the
   foreign-session commit and the `expire_on_commit=False` coupling it created.
4. `search: fence projection acknowledgement inside the caller's session` — `SearchProjectionStore`
   takes a factory-less `ClaimedRowQueue` and passes `session=self._session`. **Gotcha:** capture the
   token and item id *before* entering `begin_nested()`, since a savepoint rollback expires
   attributes touched inside it. Body records that the docstring's incompatibility claim was circular.
5. `records: fence the recompute lease on its claim tokens` — `RecomputeLease(kinds, claim_tokens)`
   in `squid/records/application/ports.py`; `complete_batch`/`fail_batch` with `max_attempts=None`;
   adds the visibility timeout. Body spells out both bugs from the audit.
6. `worker: generate the queue-health query from the queue specs`.
7. `notifications: use the shared visibility timeout and backoff`.
8. `events: claim deliveries through the shared protocol` *(optional, last)* — the design's
   falsification test. **If it needs a new spec field to fit, drop this commit and say so** — that is
   a signal to revisit the abstraction, not to add the field.

**PR 2**, after PR 1 is deployed — 9. `persistence: require a claim token whenever a row is claimed`.

**As landed**, commits 2 and 3 are one commit (they cannot compile apart — see Corrections), so PR 1
is eight commits rather than nine:

| | Commit | |
| --- | --- | --- |
| 1 | `80657335` | `persistence: give the work queues a claim token and a retry clock` |
| 2+3 | `18b502eb` | `persistence: claim through a database-minted fencing token` |
| 4 | `0614cd28` | `search: fence projection acknowledgement inside the caller's session` |
| 5 | `ec7d16e9` | `records: fence the recompute lease on its claim tokens` |
| 6 | `ca967636` | `worker: generate the queue-health query from the queue specs` |
| 7 | `f30973d6` | `notifications: use the shared visibility timeout and backoff` |
| 8 | `eb041c00` | `events: claim deliveries through the shared protocol` |

Hashes above are post-rebase (this branch was rebased 146 commits forward on 2026-08-18); the
commit content is unchanged from the 2026-08-16 landing, re-verified against current
`squid/persistence/queue.py`, `squid/schematics/infrastructure/jobs.py`, and
`squid/search/infrastructure/embeddings.py`.

Commit 8 was the falsification test and it **passed**: `domain_event_deliveries` converted using
only fields the other six specs already use, plus `claim_count`, for which it already had a column.
The abstraction is the right shape.

## Testing

`tests/integration/persistence/test_claimed_row_queue.py` needs two structural fixes before new
cases are worth adding:

- **Delete `_CREATE_SCHEMA`/`_DROP_SCHEMA` (lines 14-71) and switch to `migrated_session_factory`.**
  Hand-written DDL is exactly why the file can pass while the real schema diverges — and the headline
  regression here is *invisible* without the real triggers, because the hand-written
  `discord_sync_queue` has none.
- **Move the four domain-event tests (lines 204-279) out** to `tests/integration/events/`. They
  exercise `PostgresDomainEventRepository`, which stopped using `ClaimedRowQueue` at commit `72cca02`.

Cases, all against the migrated schema:

| Test | Asserts |
| --- | --- |
| `test_each_claimed_row_gets_its_own_token` | a batch of three yields three distinct non-null tokens and database-clock `claimed_at` |
| `test_a_reclaimed_row_cannot_be_completed_by_the_previous_holder` | `applied=False`, row survives |
| `test_a_reclaimed_row_cannot_be_failed_by_the_previous_holder` | same for `fail`; `attempts` and `last_error` unchanged |
| `test_a_lost_fence_is_logged_and_counted` | `caplog` plus a patched counter — defect 5 must not regress to silence |
| `test_a_retry_leaves_the_sync_generation_alone` | claim → `fail` → `generation` and `messages.desired_revision` unchanged. Cheap now that `c2d3e4f5a6b1` removed the trigger, and worth keeping as the regression guard against re-introducing a domain write on the retry path |
| `test_a_retry_backs_off_available_at_and_leaves_enqueued_at_alone` | replaces today's `enqueued_at > now()` assertion at :167 |
| `test_a_reenqueue_clears_the_claim_and_the_backoff` | fires the trigger on a backed-off claimed row |
| `test_a_caller_owned_claim_is_not_committed` | claim with `session=`; a second connection sees it unclaimed; roll back; still unclaimed. Encodes defect 1 |
| `test_completing_with_values_retains_the_row` | the `schematic_jobs` ack shape through the shared helper |
| `test_the_sql_and_python_backoff_agree` | `retry_delay_sql` against `retry_delay` for attempts 0-12, evaluated in Postgres |
| `test_a_scope_lease_cannot_acknowledge_work_enqueued_during_the_run` | A claims `DOOR`; re-enqueue clears the lock; B claims it; A's `complete_batch` returns 0 and B's row survives |

Elsewhere: `tests/integration/test_alembic_migrations.py` imports `QUEUE_HEALTH_SQL` at :15 and
executes it at :151 and :158 — swap to `QUEUE_HEALTH_STATEMENT`, keeping the existing eight-label
assertion as the generator's contract. Add a case that upgrades to the pre-change head, inserts a
claimed row and one with `enqueued_at = now() + interval '30 minutes'`, upgrades to head, and asserts
the backfill. Update fakes needing `claim_token` under `tests/unit/{sync,bot,worker,search,records}/`.

Note `pyproject.toml:257` — plain `pytest` runs only `tests/unit` and `tests/architecture`, so nearly
all the value here sits behind `just test-integration`, which needs Docker. Sequence the work so
those runs are cheap to repeat.

Per `AGENTS.md`: iterate with
`uv run pytest tests/integration/persistence tests/integration/test_alembic_migrations.py --no-cov`,
then finish with `just db-check`, `uv run pytest tests/unit tests/architecture`,
`just test-integration`, `uv run alembic heads`, `git diff --check`, and BasedPyright over the
changed packages.

## Corrections from implementation

Recorded 2026-08-16, after PR 1 landed. Each of these contradicts something asserted above; the
original text is left in place so the disagreement is visible.

### The branch had two Alembic heads

`b2c3d4e5f6a8` (from `509406c2`) and `c9d2e3f4a5b6` (from `b82991e8`) both descended from
`b1c2d3e4f5a7`. `alembic upgrade head` was therefore ambiguous, which broke **every** test using
`migrated_session_factory` and made `just db-check` impossible to run. Revision 1 chains off both.

Two consequences. The plan's "chain off the current head, which was `c2d3e4f5a6b1`" was already
stale by two revisions — the instruction to confirm with `alembic heads` rather than assume was
the right one and worth keeping. And because `alembic check` had been unrunnable, three table
comments had drifted from their models unnoticed (`accounts`, `discord_sync_queue`,
`vote_sessions`, from the `Principal`→`Caller` and reconciliation-queue renames earlier on this
branch). Revision 1 repairs them, because it is what makes the check runnable again.

### Column comments never worked

> "New column attribute docstrings must be mirrored by `comment=` in the migration, because
> `squid/persistence/base.py` turns them into column comments that `alembic check` compares."

The premise is false. `squid/persistence/base.py:47` reads `getattr(column, "column", None)` off an
`InstrumentedAttribute`, which has no `.column` attribute, so the guard silently skips every
column. No attribute docstring in this codebase has ever become a database comment — including
`discord_sync_queue.generation`, which predates this work.

The new columns therefore ship **without** `comment=`, matching every other column in the schema.
The docstrings stay, because they document the code. Fixing the mechanism is a real but separate
change: it would give a comment to every documented column at once and need its own migration.

### There are thirteen enqueue sites, not nine

> "Four PL/pgSQL functions ... and five Python upserts."

Five PL/pgSQL functions, and thirteen insert statements. `enqueue_starboard_sync` writes
`discord_sync_queue` from three branches, and `enqueue_discord_sync` has a second insert for the
vote sessions that embed a changed build. Locating them by name rather than by line was right;
counting the functions rather than the inserts was not, because one function can enqueue several
times.

### Commits 2 and 3 cannot be separated

> "The old `stamp`/`complete`/`fail` remain temporarily so nothing breaks."

They cannot. The new `complete` and `fail` take the same names on the same class and differ only in
that the second argument is a `uuid.UUID` rather than an `Instant`. The intermediate commit would
not have compiled. They landed as one commit, `a569c304`.

### `QueueSpec` is a plain class, not a frozen dataclass

Its value is that columns travel as `InstrumentedAttribute` so a wrong one fails to typecheck. A
dataclass field holding a descriptor is read back through `__get__`, so `spec.claimed_at` typed as
`Instant | None` — which loses exactly the checking the spec exists to provide, and produced 15
type errors. A plain `__init__` keeps the declared type on both the construction and the read.
`QueueHealthShape` stayed a dataclass; its fields are `SQLColumnExpression`, not descriptors.

### `retry_delay` could raise instead of backing off

Writing the test that pins `retry_delay(0) == 7.5s` found that `BASE_RETRY_DELAY * 2 **
(attempts - 1)` is computed before the cap, so a large enough attempt count raised `OverflowError`
out of the worker's release path. Reachable on `record_recompute_queue`, which runs with
`max_attempts=None`. Both encodings now clamp the exponent at `_MAX_DOUBLING`.

### Testing, as built

The two `SearchProjectionStore` unit tests could not survive the change — they drove a mocked
session and asserted on mutated ORM attributes, and acknowledgement is now a fenced `UPDATE`. They
moved to `tests/integration/search/test_projection_queue.py`. New files:
`tests/integration/events/test_delivery_queue.py` (the four moved domain-event cases),
`tests/integration/records/test_recompute_lease.py`,
`tests/integration/observability/test_queue_health.py`, and
`tests/unit/persistence/test_queue_backoff.py`.

`tests/integration/records/test_recompute_lease.py` needs an autouse fixture that empties
`record_recompute_queue`: migrations seed a door and an extender rebuild, which otherwise ride
along in every claim.

**Left failing, and not caused by this work.** After PR 1: `tests/unit` and `tests/architecture`
are 1916 passed / 1 skipped; `tests/integration` is 309 passed / 3 failed. All three failures are
pre-existing on this branch and none touches a file these commits changed.

- `test_idempotency_encryption_migration_purges_plaintext_replay_rows` — its raw `INSERT` names an
  `idempotency_requests.caller` column that does not exist at the historical revision it upgrades
  to. Fallout from the `Principal`→`Caller` rename in `c59db12d`; it fails identically with these
  commits stashed.
- `test_the_schema_rejects_kind_threshold_combinations_the_domain_forbids[build-None-None]` — the
  hand-written `vote_schema` fixture in that file has not kept up with `b2c3d4e5f6a8`, which made
  the vote thresholds nullable, so the constraint under test no longer exists in the fixture's DDL.
  Another instance of the hand-written-DDL problem the queue test was moved off.
- `test_disposable_api_stack_resets_every_mutable_store_and_cleans_up`.

Worth noting that the first two were *invisible* before this work: with two heads, the migration
test file and everything else using `migrated_session_factory` errored during setup rather than
running.

## Open questions

- **Does `media` justify `renew()` on the shared protocol?** Deferred until a second caller needs a
  lease heartbeat. If none appears, media keeps its own adapter and that is the right outcome.
- **Should `VISIBILITY_TIMEOUT` be per-queue?** It is one constant for seven queues whose slowest
  handlers differ by orders of magnitude — schematic rendering against a Discord edit. `QueueSpec` is
  the natural place for an override, but there is no evidence yet that five minutes is wrong for any
  specific queue, so this stays a single constant until an incident says otherwise.
- **Does `record_recompute_queue` want dead-lettering?** It currently has none, and this design keeps
  `max_attempts=None` for it. That is deliberate — a stuck recomputation is a visible staleness bug,
  where a dead-lettered one is invisible — but it means the queue can spin forever on a poisoned
  scope. Revisit if `squid.queue.oldest_ready_age` for it ever alerts.
