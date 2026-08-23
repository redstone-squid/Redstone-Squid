# 43 — Candidate atomicity and lock discipline

## Problem

The overhaul review at `fc677a41` found four related seams in `Mount`. Presentation,
handlers, generation, assets and session updates already crossed the staged-candidate commit
point, but shared observations and shared-write dirtiness did not. Separately, slow-action
feedback could hold acknowledgement behind the render lock, and an awaitable presented hook
could deadlock by re-entering that same lock.

The governing rule is:

> Staging may provisionally acquire what a candidate needs, but only successful delivery may
> retire what the visible generation still depends on.

## 1. Candidate-atomic shared observations

`_Candidate.tree.observations` is already the candidate-local read set. The mount keeps three
different views of it:

- `_observed`: the committed generation's reads, exposed by `Mount.observed`;
- `_watched`: insertion-ordered committed reads union everything staged since the last
  successful commit;
- `_follows`: topics actually subscribed through a `TopicFollower` scheduler.

`_draw` calls `_ensure_follows`, which extends `_watched` and acquires missing subscriptions
but removes nothing. `_commit` calls `_prune_follows`, which publishes the delivered
candidate's observations, resets `_watched` to them and retires obsolete subscriptions.
Rollback intentionally does not prune because another outstanding candidate may need the
same provisional follow. `finish` retains its existing guard and teardown clears all three.

This closes the unsafe A→B failure: while a B candidate awaits Discord, the message still
shows A and remains subscribed to A. A failed edit leaves A live; a successful one earns the
right to unsubscribe it.

## 2. Shared writes use the render-input revision

`ComponentRuntime.revision` names all render inputs, not component state alone. When an action
commits a write to any address in `_watched`, `_note_shared_writes` calls
`runtime.invalidate()`. The candidate records the revision it rendered against, and
`runtime.commit(..., rendered_revision=...)` leaves the mount dirty if that revision moved
while delivery was in flight.

This is ordered correctly on both sides of the race:

- note then candidate commit: the candidate's old revision differs, so it stays dirty;
- candidate commit then note: the commit clears first and invalidation dirties it again.

Consulting `_watched`, rather than only committed `_observed`, also covers a write to a cell
newly read by an in-flight candidate. The candidate may still land, but it cannot claim to be
current.

## 3. Acknowledgement is independent of painting

The dispatch task group owns two watchdog legs:

1. an absolute `acknowledgement_timeout` sleep followed by deferral;
2. for actions with feedback, a `pending_after` sleep followed by the busy paint.

Visible resource work deliberately holds `_render_lock` across I/O. The paint leg may wait
there, but the acknowledgement leg never does. A `deferred_message_update` still addresses
the source message, so the late busy paint receives a standing interaction handle and can
land as an edit. `_DispatchProfile.acknowledge` is first-wins because a later busy edit must
not relabel the already-finished watchdog acknowledgement span. `Mount` rejects acknowledgement
timeouts outside `0 < timeout < 3`, matching `Router` and Discord's deadline.

The modal-submit watchdog stays single-stage: it has no busy-paint leg and was already an
absolute timeout.

## 4. Commit observers and middleware

`PresentedHook` is synchronous by design. `_commit_presented` invokes observers immediately
after local commit while still under the render lock, catches each exception independently,
and never awaits arbitrary host code. A durability observer only marks a checkpoint pending,
which is already synchronous. External hosts needing async follow-up enqueue work or hand it
to their owned supervisor. `on_finish` remains async because finish hooks run outside the lock
and are allowed to cascade.

Action middleware remains outside `_action_transaction`. That placement is required for a
policy layer to catch commit-time errors such as `SharedStateConflictError`; it also means
component state written by middleware is not part of the handler transaction and will not
roll back with it. The API and README state that middleware is a policy surface unless an
independent state write is intentional.

## Verification

- `test_shared_follow.py`: failed A→B delivery retains A; successful delivery retires it;
  shared writes racing ordinary and newly observed candidate inputs survive the revision
  fence.
- `test_mount.py::TestBusyFeedback`: a held render lock cannot delay deferral; once released,
  the late busy paint lands and restore returns the committed scene. Invalid mount timeouts
  are rejected.
- `test_mount.py::TestPresentedHooks`: synchronous invalidation leaves the mount usable and
  a raising observer is logged and swallowed.
- `test_durable_runtime.py`: the synchronous durability observer retains checkpoint behavior.
- The full `packages/squid-layouts/tests` suite, `tests/unit/bot`, `just typecheck`, and
  `git diff --check` are the final regression gates.

The live `/layout shared` slow-visible-resource check remains manual because unit doubles do
not reproduce Discord's three-second client failure end to end.

## Out of scope

[42](42-redundant-edits.md) remains a problem statement. Reliable self-write repainting makes
its extra edit more consistently visible, but correctness precedes edit deduplication.
