# Record recomputation

> **Status:** proposed, not started. This came out of closing [durable queues](completed/durable-queues.md),
> whose open question about dead-lettering `record_recompute_queue` turned out to be the wrong question.

## Problem

Records (the fastest door, the smallest extender, and so on) are derived data. `RecordComputationService.rebuild`
(`squid/records/application/services.py`) reloads every confirmed build of a kind, recomputes every category and
publishes a replacement run. Whenever a source change makes a kind's records out of date,
`record_recompute_queue` tells the worker to rebuild that kind.

That table is set up as a durable work queue, but it acts as a per-kind "needs rebuilding" flag. The queue
machinery adds cost without adding anything, and the rebuild runs in one transaction that is longer than it needs
to be.

### The queue only carries a kind

- **The consumer reads only `build_kind`.** `process_queue` claims rows, reduces them to a set of kinds, and rebuilds
  each kind in full. It never reads `scope_key`, `build_id` or `reasons`.
- **There is effectively one row per kind.** The `enqueue_build_search_projection` trigger in
  `squid/persistence/postgres_entities/functions/enqueue_build_search_projection.sql` upserts with `scope_key` set to
  the kind. Each change overwrites `build_id` with the most recent build, so the column records nothing useful.
  `active_rules_revision_id` enqueues the same keys.
- **Kinds succeed and fail together.** All claimed kinds are rebuilt under one lease, and `fail_batch` releases
  every row in it. An extender failure makes doors rebuild again for no reason.
- **The lease can expire while a rebuild is still running.** Claiming, rebuilding and acknowledging use three
  separate transactions, connected by the shared five-minute `VISIBILITY_TIMEOUT`. A rebuild that runs longer than that
  lets a second worker claim and rebuild the same kind concurrently.
- **Failures are hard to see.** Dead-lettering would be wrong here: it would mean "stop computing door records".
  The rebuild is deterministic, so a failure is a bug that blocks every record of that kind until someone fixes it.
  Retrying forever is correct, but nothing alerts on it. Each source change resets `attempts` to 0, so a broken kind
  retries a full rebuild after every build edit. When nothing changes, the backoff grows to its cap of
  15 s × 2¹², about 17 hours.

### The rebuild transaction is longer than it needs to be

`rebuild_in` runs these steps in the caller's transaction:

1. `active_rules_revision_id` takes `pg_advisory_xact_lock('record-rules-revision-activation')`. A transaction-level
   lock is held until commit.
2. `list_confirmed` and `list_requested_categories` load the source data.
3. `_compute_scope` generates the category combinations. This is synchronous Python running on the event loop.
4. `activate` takes `record-run:{kind}:{version}` and writes the run, results, holders and history.

Only step 4 has to be atomic. Holding the transaction across steps 1-3 has two costs:

- **Every records operation waits on one global lock.** The worker drain, the staff rebuild form
  (`squid/bot/submission/records.py`) and `RecordService.lookup_or_materialize` all call
  `active_rules_revision_id`. While a background rebuild of one kind holds the lock, a user's lookup for another kind
  waits for the whole rebuild to finish.
- **The reads are not consistent anyway.** We run at READ COMMITTED, so each read statement in step 2 sees its own
  snapshot.

The long transaction does do one useful thing, by accident. Because the global lock serializes rebuilds, a rebuild that
read older data can never publish after one that read newer data. Any redesign has to keep that guarantee.

## Proposal

Do part 1 regardless of how long rebuilds take. Part 2 depends on the measurement below.

### 1. Replace the queue with a per-kind "needs rebuilding" flag

```
record_dirty_kinds(build_kind PK, generation bigint, dirty_since, attempts, available_at, last_error)
```

- **Producers** (the trigger, rules-revision activation, and saving a requested category) upsert the kind and
  increment `generation`.
- **The worker** handles each kind in its own transaction:
  1. Take `pg_try_advisory_xact_lock(kind)`. If another worker holds it, skip the kind.
  2. Read `generation` as `g`.
  3. Rebuild the kind.
  4. Run `DELETE ... WHERE build_kind = :kind AND generation = :g`, then commit.
- **On failure**, roll back, then record `attempts`, `available_at` and `last_error` in a separate short transaction.

How this answers each problem above:

- **No lease.** If the worker crashes, its transaction rolls back and the flag is still set. The advisory lock is
  released automatically, so we need no visibility timeout, no claim token and no claim-complete check.
- **Changes made mid-rebuild are not lost.** A change that arrives during the rebuild increments `generation`, so the
  final `DELETE` matches nothing and the kind runs again. The claim token does this job today.
- **Kinds fail independently.**
- **Failures become visible.** Add an alert on `attempts`/`last_error`. Decide whether a source change should reset
  `attempts`. Today it does, and that is what makes a broken kind retry on every build edit.

This takes `record_recompute_queue` out of `ClaimedRowQueue`. The durable-queues audit brought it in to fix two real
bugs: acknowledging a lease deleted work enqueued during the run, and a crashed worker's lease was never reclaimed.
The generation check and the transaction-scoped lock fix the same two bugs without a lease. `squid/worker/queue_health.py`
needs its own small entry for the new table, so it keeps reporting ready count and oldest age.

### 2. Shorten the rebuild transaction (if measurements justify it)

1. **Read from one snapshot.** Open a short `REPEATABLE READ READ ONLY` transaction. Read `generation` first, then
   every input: candidates, requested categories, the rules revision and the current-version pin. Then close it.
2. **Compute with no transaction open.** Use `to_thread` if profiling shows `_compute_scope` blocks the event loop.
3. **Publish in a short transaction:**
   - Take `record-run:{kind}:{version}`.
   - Refuse to activate if the active run was computed from a newer `generation`. Each run stores the generation it
     was computed from, so an older result can never replace a newer one.
   - Write the run.
   - Clear the flag only where `generation = g`.

Separately, `active_rules_revision_id` should lock only when it has to activate a new revision. When a plain read shows
the expected revision is already active, it should return without locking. That is almost every call.

Part 2 depends on part 1, because the generation has two jobs: it decides when the flag can be cleared, and it orders
publication. A test should list every input to a rebuild and check that changing each one increments the generation.
If an input is missing, a stale run can be published and nothing reports it.

## Before starting

- **Measure.** Find the distribution of `squid.worker.record_maintenance` span durations in production. If full
  rebuilds take tens of milliseconds, the lock contention does not matter and part 2 is not worth its complexity.
- **Decide whether incremental, per-build recomputation is planned.** If it is, a real queue keyed by build is the
  right shape and part 1 is wrong. Nothing on the roadmap suggests it is.
- **Check `activate` under concurrency.** Today the global lock prevents two rebuilds of the same kind and version
  from overlapping. Part 2 removes that, so confirm the per-run advisory lock and the `is_active` switch are safe
  without it.

## Already done

These came out of the same review and landed with the durable-queues close-out:

- Removed `RecordRepository.enqueue`. Nothing called it, and its `kind:build_id` keys did not match the keys the real
  producers use.
- Revision `f3c6b9e4a1d7` now gives a token to any claim made without one before it adds the `<table>_claim_complete`
  checks.
