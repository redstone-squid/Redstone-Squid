# Record recomputation

> **Status:** proposed, not started. This came out of closing [durable queues](completed/durable-queues.md),
> whose open question about dead-lettering `record_recompute_queue` turned out to be the wrong question.

## Problem

Records (the fastest door, the smallest extender, and so on) are derived data. `RecordComputationService.rebuild`
(`squid/records/application/services.py`) reloads every confirmed build of a kind, recomputes every category and
publishes a replacement run. Whenever a source change makes a kind's records out of date,
`record_recompute_queue` tells the worker to rebuild that kind.

Three things are wrong with this today:

1. The table is set up as a durable work queue, but it acts as a per-kind "needs rebuilding" flag. The queue
   machinery adds cost and failure modes without adding anything.
2. Every records operation waits on one global lock for the length of a full rebuild.
3. We rebuild far more often than the records change, and every rebuild rewrites every row.

### The queue only carries a kind

- **The consumer reads only `build_kind`.** `process_queue` claims rows, reduces them to a set of kinds, and rebuilds
  each kind in full. It never reads `scope_key`, `build_id` or `reasons`.
- **There is effectively one row per kind.** The `enqueue_build_change` trigger in
  `squid/persistence/postgres_entities/functions/enqueue_build_change.sql` upserts with `scope_key` set to
  the kind. Each change overwrites `build_id` with the most recent build, so the column records nothing useful.
  `active_rules_revision_id` enqueues the same keys.
- **Kinds succeed and fail together.** All claimed kinds are rebuilt under one lease, and `fail_batch` releases
  every row in it. An extender failure makes doors rebuild again for no reason.
- **The lease can expire while a rebuild is still running.** Claiming, rebuilding and acknowledging use three
  separate transactions, connected by the shared five-minute `VISIBILITY_TIMEOUT`. A rebuild that runs longer than that
  lets a second worker claim and rebuild the same kind concurrently.
- **Failures are hard to see.** Dead-lettering would be wrong here: it would mean "stop computing door records".
  The rebuild is deterministic, so a failure is a bug that blocks every record of that kind until someone fixes it.
  Retrying forever is correct, but nothing alerts on it. Each source change resets `attempts` to 0, so the count never
  shows how long a kind has been broken. When nothing changes, the backoff grows to its cap of 15 s × 2¹², about
  17 hours.

### One global lock covers the whole rebuild

`rebuild_in` runs these steps in the caller's transaction:

1. `active_rules_revision_id` takes `pg_advisory_xact_lock('record-rules-revision-activation')`. A transaction-level
   lock is held until commit.
2. `list_confirmed` and `list_requested_categories` load the source data.
3. `_compute_scope` generates the category combinations. This is synchronous Python running on the event loop.
4. `activate` takes `record-run:{kind}:{version}` and writes the run, results, holders and history.

The worker drain, the staff rebuild form (`squid/bot/submission/records.py`) and `RecordService.lookup_or_materialize`
all go through step 1. While a background rebuild of one kind holds the lock, a user's lookup for another kind waits
for the whole rebuild to finish.

The lock does one useful thing, by accident. Because it serializes rebuilds, a rebuild that read older data can never
publish after one that read newer data. Any redesign has to keep that guarantee.

The long transaction itself is not the problem. It holds no row locks until step 4, and at READ COMMITTED it pins no
snapshot between statements. The problem is the scope of the lock, and step 3 blocking the event loop. The staff form
and `lookup_or_materialize` run step 3 inside the bot process, where a long synchronous computation delays every
other Discord interaction and the gateway heartbeat.

### Most rebuilds change nothing, and each one rewrites everything

- **The trigger fires for builds that cannot affect records.** It fires on any change to any door or extender build,
  but `list_confirmed` reads only confirmed builds. Every edit to a pending submission rebuilds the whole kind.
- **Every rebuild publishes a full new run.** `activate` inserts a new run with all its results, holders and history,
  and publishes `record_run.activated`, even when the computed records equal the active run's.
  `record_computation_runs` also has an enqueue-search trigger, so each activation creates search projection work.
- **Superseded runs appear to be kept forever.** Nothing in `squid/records` or the worker deletes them, so the
  result, holder and history tables grow with every build edit.

## Proposal

Steps 1-3 are independent of how long rebuilds take. Step 4 is the larger saving and depends on the measurement
below. Step 5 is deferred.

### 1. Replace the queue with one state row per kind

```
record_kind_state(build_kind PK, generation bigint, built_generation bigint,
                  dirty_since, attempts, available_at, last_error)
```

The migration creates one row for each kind and the rows are never deleted. A kind needs rebuilding when
`generation > built_generation`.

`generation` only ever increases. That is why the row is permanent: if the worker deleted the row to clear the flag,
the next change would insert a new row and the counter would start again. A counter that can go backwards cannot be
used later to order runs (step 5), and a mistake in a `DELETE` condition would silently lose a change.

- **Producers** (the trigger and rules-revision activation) increment `generation`, set `available_at` to now, and set
  `dirty_since` if it is null. They do not touch `attempts` or `last_error`.
- **The worker** handles each kind that needs rebuilding in its own transaction:
  1. Try to take the kind's rebuild lock (step 2). If another rebuild holds it, skip the kind.
  2. Read `generation` as `g`, and read the current-version pin.
  3. Rebuild the kind.
  4. Set `built_generation = g`, reset `attempts` and `last_error`, and clear `dirty_since` if `generation` is still
     `g`. Commit.
- **On failure**, roll back, then increment `attempts` and record `available_at` and `last_error` in a separate short
  transaction.

How this answers each problem above:

- **No lease.** If the worker crashes, its transaction rolls back and the kind still needs rebuilding. The advisory
  lock is released automatically, so we need no visibility timeout, no claim token and no claim-complete check.
- **Changes made mid-rebuild are not lost.** A change that arrives during the rebuild increments `generation` past
  `g`, so the kind still needs rebuilding after the worker commits. The claim token does this job today.
- **Kinds fail independently.**
- **Failures become visible.** A source change makes the kind retry immediately, because the new data may be what
  fixes the failure. It does not reset `attempts`; only a successful rebuild does. So `attempts` counts consecutive
  failures and `dirty_since` shows how long the records have been out of date. Alert on both.

Only the worker advances `built_generation`. The staff form and `lookup_or_materialize` can rebuild a scope other than
the pinned current version, so they leave the row alone. The cost is one extra worker rebuild when a staff rebuild
happens to run while the kind already needs rebuilding.

This takes `record_recompute_queue` out of `ClaimedRowQueue`. The durable-queues audit brought it in to fix two real
bugs: acknowledging a lease deleted work enqueued during the run, and a crashed worker's lease was never reclaimed.
The generation check and the transaction-scoped lock fix the same two bugs without a lease. `squid/worker/queue_health.py`
needs its own small entry for the new table, so it keeps reporting how many kinds need rebuilding and for how long.

### 2. Lock per kind instead of globally

- **`active_rules_revision_id` locks only to activate.** A plain read first checks whether the expected revision is
  already active, and returns it without locking. That is almost every call. Otherwise it takes the lock and checks
  again before activating.
- **Every rebuild takes a per-kind lock.** `rebuild_in` takes `pg_advisory_xact_lock('record-rebuild:{kind}')` before
  it reads anything for that kind, and holds it until commit. The worker uses the try-lock form and skips a busy
  kind. The staff form and `lookup_or_materialize` wait. A caller that rebuilds several kinds locks them in a fixed
  order so two callers cannot deadlock.

Rebuilds of one kind still run one at a time, from reading to publishing, so an older read still cannot publish after
a newer one. Rebuilds of different kinds no longer wait for each other. The existing `record-run:{kind}:{version}`
lock in `activate` becomes redundant but harmless.

Two inputs do not go through the build trigger, and the lock is what keeps them correct:

- **Requested categories.** `lookup_or_materialize` saves the category and rebuilds in one transaction. It should take
  the kind lock before saving, so the save and the run that includes it commit together.
- **The current-version pin.** The worker reads it in a separate transaction today, before the rebuild starts. Step 1
  moves that read under the kind lock.

The rule for any future input: a change either increments `generation`, or is published by the same transaction that
makes it, under the kind lock. A test should list every input to a rebuild and check that each one follows the rule.
If an input is missed, records stay out of date and nothing reports it.

### 3. Compute off the event loop

Run `_compute_scope` with `asyncio.to_thread` if it takes more than about 50 ms. It is a pure function of data already
loaded, so this needs no other change. It matters most in the bot process.

### 4. Publish only when the records changed

Before `activate`, compare the computed batch with the active run for the same kind and version. If they are equal,
skip the write: no new run, no `record_run.activated` event, no search projection work. Most rebuilds then only read.

The comparison has to cover everything a run stores: the rules revision, titles, statuses, gap reasons, holders with
their metric snapshots, and history. A stored hash of the batch on the run is probably simpler than loading and
comparing rows. A new rules revision must always publish.

Skipping unchanged runs is safer than making the trigger ignore unconfirmed builds. A trigger filter has to get every
status transition right, and a mistake there leaves records out of date without any error.

Also delete superseded runs after a retention period, once we have confirmed nothing reads them.

### 5. Deferred: compute outside the transaction

The earlier version of this plan split the rebuild into a `REPEATABLE READ READ ONLY` snapshot read, a computation
with no transaction open, and a short publish transaction that refuses to replace a run computed from a newer
`generation`. Compared with step 2, that adds two things:

- All inputs come from one snapshot. Today each read statement sees its own, so a build edited mid-read can be loaded
  half-changed. The edit also increments `generation`, so the next rebuild corrects it, but the wrong run is public
  until then.
- No database connection is held during the computation.

It costs a stored generation on every run, an ordering check in `activate`, and the loss of the simple "one rebuild at
a time per kind" argument. Do it only if rebuilds still take seconds after steps 1-4, or if a half-read run is
actually observed.

## Before starting

- **Measure.** Find the distribution of `squid.worker.record_maintenance` span durations in production, how many
  rebuilds run per day, and how many produce records that differ from the previous run. Durations decide step 3. The
  other two numbers decide how much step 4 saves.
- **Decide whether incremental, per-build recomputation is planned.** If it is, a real queue keyed by build is the
  right shape and step 1 is wrong. Nothing on the roadmap suggests it is.
- **Confirm nothing reads superseded runs** before adding retention in step 4.

## Already done

These came out of the same review and landed with the durable-queues close-out:

- Removed `RecordRepository.enqueue`. Nothing called it, and its `kind:build_id` keys did not match the keys the real
  producers use.
- Revision `f3c6b9e4a1d7` now gives a token to any claim made without one before it adds the `<table>_claim_complete`
  checks.
