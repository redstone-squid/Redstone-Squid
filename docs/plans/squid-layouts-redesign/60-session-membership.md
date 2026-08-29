# 60 — Session membership: members and per-session capacity

## Status

Shipped. Membership is `sessions.py`; the record protocol is `durability/session_records.py`;
the durable path is `durability/runtime.py`; the worked consumer is `/layout lobby` in
`squid/bot/layout_showcase.py`. This closes [90](90-deferred.md)'s "Participant tracking /
shared sessions" entry on [34](../completed/squid-layouts-redesign/34-safe-session-runtime.md)
§B.4's terms.

The build differs from the first draft in four places, and each difference is a section below:
capacity is an `int` rather than a `ParticipantPolicy` (§2), the durable write follows `_attach`
instead of fencing inside the lock (§4), the four result types collapsed to two (§3), and the
worked consumer is a real command rather than a test fixture (§6). Writing that consumer found
a framework defect the draft could not have predicted (§7).

## The problem

`SessionPolicy` controls how many logical sessions may occupy one `SessionKey`. Nothing
controlled how many users may participate in one session:

```text
SessionPolicy  = how many sessions may occupy this key?
capacity       = how many users may join this session?
```

Worse, `SessionSummary.participants` carried no information. `Session.__init__` set it to
`{actor_id}` and nothing ever changed it, so `victim.participants - {actor_id}` in
`ProtectCrossUserAttachments` was provably always empty: cross-user replacement protection
rested entirely on `attachment_actors`. The field was either dead weight to delete or a model
to finish. This finishes it.

## 1. Membership is a session operation

```python
opened = await sessions.open(
    lobby.mount(),
    destination,
    key=SessionKey.guild("lobby", guild.id),
    actor_id=interaction.user.id,
    capacity=4,
)

result = await opened.session.join(other_user_id)
left = await opened.session.leave(other_user_id)
```

Membership is caller-authorized. The framework decides capacity and atomicity, never whether a
user was invited, banned, paid, or assigned to a team.

The opener is the initial member when `actor_id` is present; an actorless session starts empty.
Attachment actors remain operational attribution and never join implicitly — opening a child
screen or pressing a control is not evidence that somebody accepted membership. Leaving the
opener or the final member is allowed, and an empty session stays alive: only the application
knows when a lobby is over.

`participants` is now **derived** — `members | attachment_actors | {actor_id}` — so a protection
policy reads one field. Before, a third-party `ReplacementProtection` had to remember to union
three of them, and the shipped one did.

## 2. Capacity is an `int`; other rules are per-call predicates

The draft proposed `SessionPolicy(participants=ParticipantPolicy(limit=10))`. Two things were
wrong with it.

`SessionPolicy` governs the key contest — `_open_locked` consults it only when a key is present
— while capacity applies to a keyless session too, so nesting them leaves half the type dead
depending on an unrelated argument, and spells one integer as three nested constructors. Capacity
is therefore a sibling `capacity=` on `open` and a flat `Screen.capacity` field.

More importantly, a bare limit does not deliver the atomicity the feature exists for. A caller
whose rule is anything else — teams, bans, one-game-at-a-time — checks it and *then* calls
`join()`, and the race reopens between the two. So an admission rule decomposes:

- **Facts about the candidate** (banned, consented, paid) are async and I/O-bound but independent
  of who else is present, so concurrent joins cannot invalidate each other's answers. They belong
  in the caller. The lifecycle lock also serialises `attach` and `finish`; a round-trip under it
  is the defect shape [64](64-challenged-admission.md) was written around, one layer down.
- **Facts about the set** (capacity, uniqueness, team balance) are pure functions of `members` and
  must be atomic. They belong under the lock, and they need no I/O to be.

`join(user_id, when=...)` takes the second half as a synchronous pure predicate over the current
members. The showcase lobby uses `when=lambda members: self.host_id in members` — a rule that
depends on the roster, cannot be expressed as a number, and would race if checked outside.

For the residual case — a rule that is *both* async and set-dependent, such as "max two per team
where teams live in Postgres" — the answer is optimistic concurrency rather than in-lock I/O:

```python
while True:
    members = session.members
    if not await team_rule(members, user_id):
        return refuse()
    result = await session.join(user_id, expect=members)
    if result.status is not MembershipStatus.CONFLICT:
        break
```

`expect=` costs one comparison under the lock and one status value, and it is what makes the
synchronous predicate a decomposition rather than a limitation.

## 3. One result type

```python
class MembershipStatus(StrEnum):
    JOINED = "joined"
    ALREADY_MEMBER = "already_member"
    LEFT = "left"
    NOT_MEMBER = "not_member"
    AT_CAPACITY = "at_capacity"
    REFUSED = "refused"            # the caller's `when=` declined
    CONFLICT = "conflict"          # `expect=` did not match
    SESSION_FINISHED = "session_finished"

@dataclass(frozen=True, slots=True)
class MembershipResult:
    user_id: int
    status: MembershipStatus
    members: frozenset[int]
    remaining_capacity: int | None

    @property
    def committed(self) -> bool: ...
```

The draft had `JoinResult`/`LeaveResult` — field-identical — and two status enums sharing
`SESSION_FINISHED`. Four public types for one operation, in a package whose idiom is unions of
frozen dataclasses. Results carry immutable snapshots so a caller can render the outcome it
received even if the session moves afterwards.

Order under the lock, both operations: session alive → `expect` matches → membership → capacity →
`when` → commit. The idempotent answers precede capacity, so re-joining a full lobby is
`ALREADY_MEMBER` rather than an error.

## 4. Durability follows `_attach`

`DurableSessionRecord` advances to protocol 2 and carries the member set and the capacity beside
the mount graph. Protocol 1 records predate membership and decode with an unbounded capacity and
the stored opener as their only member, which is what they meant; the next checkpoint rewrites
them at protocol 2. The summary payload needs no version field — a payload without a `members`
key is a legacy one by construction.

A durable join commits under the lifecycle lock and checkpoints **after releasing it**, exactly
as `_attach` has always done. The draft demanded an immediate fenced checkpoint inside the lock,
rollback on failure, a `ParticipantPersistenceError`, and deferred teardown. Those last two exist
only to survive a deadlock the first two create: a failed `save()` calls `_lose_claim`, which
finishes the session, which takes the lock the operation still holds. `test_a_membership_
checkpoint_that_loses_the_claim_finishes_without_deadlocking` times out if the checkpoint is moved
back inside.

The accepted cost, stated plainly: a crash between a join and its checkpoint loses that join, so a
recovered lobby can briefly sit above its capacity. This is what already happens to an attachment,
a failed checkpoint is visible as `DurabilityHealth.CHECKPOINT_PENDING` and retried by
maintenance, and a caller needing read-your-write awaits `DurableSessionRuntime.flush()`.

Two things the draft was right about and this keeps: recovery validates that the stored summary
and record agree on membership before registering, and checkpoint writes for one session are
serialized so a slower writer cannot land an older snapshot after a newer one. That second race
predates membership — `_attach` has always checkpointed outside the maintenance lock — but joins
are frequent enough to make it ordinary rather than theoretical.

## 5. Inspection

`SessionInspection` carries `members`, `capacity` and `remaining_capacity` beside the derived
`participants`, and the devtools render `used/limit` on both the session list and the detail
panel: the question an operator asks about a lobby is whether it is full. No devtools join/kick
operation is added — operational mutation needs its own authorization and audit design.

## 6. The worked consumer

34 §B.4 said that "without that worked consumer, participant indexing is omitted rather than
guessed at from Cascade's API", and 90 made a lobby/game example the removal condition. The draft
substituted a test-only fixture and explicitly no bot UX, which inverts that gate. `/layout lobby`
is the consumer: a four-seat guild lobby that renders `session.members`, holds no roster of its
own, opens Join to everyone and lets Start consult the roster itself — the exact split 34 §B.2
describes for an open lobby with member-only controls.

## 7. What the consumer found

Delivery renders. The registry indexed a root mount only *after* delivery returned, so a component
drawing session facts found no session on its own first paint and was never asked to draw again —
the lobby rendered its "closed" branch and stayed there. Fixed by indexing before `mount.send` and
unindexing on an abandoned delivery. No amount of reading would have produced this; a test fixture
that constructs its session first would not have either.

## Not included

- No invitation, role, team, ready-state, ban, or member-metadata model.
- No framework-level authorization callback.
- No minimum member count or automatic finish when empty.
- No automatic join from attachment, access, or interaction activity.
- No member-specific mount access changes.
- No waiting queue when capacity is full.
- No transfer of membership between sessions.
- **No cross-session member index.** 34 §B.4 also asked that joining be atomic across every scope
  a member occupies, so a session could refuse a user already in another one. That is the part
  only the registry can do — per-session capacity needs one lock, this needs two — and it is a
  lock-ordering problem with no consumer today. Recorded rather than dropped.
- No database schema migration; only versioned durable JSON payloads change.

## Verification

`tests/test_sessions.py::TestMembership`, `tests/test_screens.py`,
`tests/test_durable_runtime.py`, `tests/test_operations.py`, and
`tests/unit/bot/test_layout_showcase.py::TestLobby`.

Not run locally: `just typecheck` and the full suite, which exhaust this machine's memory. CI
owns both.
