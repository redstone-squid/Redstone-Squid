# 66 — Ownership boundaries: make resource death explicit

## Problem

An external design review (2026-08-24) proposed ten "make ownership explicit at the boundary"
changes. Checked against the tree, most described decisions already made: `MountAddress` vs
`EditHandle` is its identity/authority split verbatim (plan 07/23), plan 28 already separates a
transactional `StateDelta` from an author-supplied external inverse, and plan 53 already
enforces adoption as a move. Session-owns-mounts was in flight as [60](60-session-membership.md).

What the review was worth is its diagnostic question, not its ten answers:

> Who owns this, when does that ownership begin, and what exact event ends it?

Asked of the three background-task owners in the packages, it found a confirmed bug.

## What it found

`PersistedPool` entered an anyio task group inside `load()` and exited it inside `close()`.
anyio binds a task group's cancel scope to the entering task, so this only worked when both ran
in the same task — while the intended usage is the opposite: `load()` belongs to whichever
request first needed the namespace, `close()` to shutdown. Worse than a noisy close: a `load()`
from a child task pushed the pool's scope onto that task and never popped it, so the *loading*
task could not exit its own scope. All three tests called both from one test task.

`_Replacement.apply()` carried `assert self._baseline is not None` because `prepare()` left what
it built in a field `apply()` had to trust — a contract documented as past the point of failure,
enforced by a runtime check.

## What shipped

1. **`stores: give PersistedPool a supervised run loop`** — `run()` owns the worker for its
   whole life, matching `Reactor.run` and `PostgresTopicBridge.run`; the host supervises it.
   `load()` on a pool that is not running raises. `close()` is terminal in both directions: it
   refuses later loads even for a hydrated scope, and a commit arriving afterwards is reported
   rather than queued for a worker that no longer exists.
2. **`reactive: hand prepared work from prepare to apply`** — `ActionParticipant[PreparedT]`;
   `prepare()` returns what `apply(prepared)` takes. Breaking, and authorized: squid-reactive is
   0.1.0 and unpublished, and `_Replacement` is its only implementation outside tests.
   `abort`/`finalize` keep their no-argument shape, having no invariant that can fail.
3. **`layouts: settle a render candidate exactly once`** — a `settled` flag and one guard across
   `_commit_render` and `_rollback`, the discipline `SubscriptionReconciler` already keeps.
4. **`sessions: make a mount's membership one record`** — one insertion-ordered
   `dict[Mount, _Membership]` replaces a list and two parallel dicts that `attach` wrote and
   `_detach` unwound separately.

## What it did not need

The other half of unit 3's invariant — that only one candidate is outstanding — is already
enforced a layer down: `_draw` stages subscriptions and the reconciler refuses a second staged
set, so two live candidates cannot coexist and a stale one cannot rewind the live generation. A
check for it was written, found unreachable, and removed rather than shipped untested.

Everything else the review proposed is recorded in [90](90-deferred.md) with its reason.

## Status

Shipped 2026-08-24.
