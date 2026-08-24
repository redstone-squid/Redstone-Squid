# 60 — Session membership: participants and per-session capacity

## Problem

`SessionPolicy` controls how many logical sessions may occupy one `SessionKey`. It does not
control how many users may participate in one session:

```text
SessionPolicy      = how many sessions may occupy this key?
ParticipantPolicy  = how many users may join this session?
```

Games, lobbies, collaborative editors, and review rooms therefore have no framework operation
for admission. Applications can keep their own participant set in component state, but then
admission races with concurrent clicks, replacement protection cannot see it, inspection reports
the wrong users, and durable recovery can lose the set.

The last problem already exists in a latent form. `Session.participants` is serialized in durable
summaries and is used by replacement protection, but recovered `Session` construction currently
rebuilds it from only the root `actor_id`. Membership belongs to the logical session, not to one
mount or component tree, so it needs a first-class session operation and durable representation.

## Decision

Make membership a serialized operation on `Session`, with capacity nested inside the existing
opening policy:

```python
mount = sessions.defaults.mount(Lobby(...), access=Owner(interaction.user.id))
opened = await sessions.open(
    mount,
    destination,
    key=SessionKey.guild("game", guild.id),
    policy=SessionPolicy(participants=ParticipantPolicy(limit=10)),
    actor_id=interaction.user.id,
)

result = await opened.session.join(other_user_id)
left = await opened.session.leave(other_user_id)
```

Keeping participant capacity inside `SessionPolicy` gives `SessionRegistry`, `Screen`, and
`DurableSessionRuntime` one opening-policy path. It avoids a parallel `participant_policy`
argument and ensures the policy is carried consistently through ordinary opening, replacement,
durable admission, and recovery.

Membership operations are caller-authorized and capacity-only. The framework does not decide
whether a user was invited, banned, paid, assigned to a team, or otherwise allowed to join. The
caller performs that authorization before invoking `join()` or `leave()`.

## Public API

`squid_layouts.discord.sessions` adds:

```python
@dataclass(frozen=True, slots=True)
class ParticipantPolicy:
    limit: int | None = None

class JoinStatus(StrEnum):
    JOINED = "joined"
    ALREADY_JOINED = "already_joined"
    LIMIT_REACHED = "limit_reached"
    SESSION_FINISHED = "session_finished"

@dataclass(frozen=True, slots=True)
class JoinResult:
    user_id: int
    status: JoinStatus
    participants: frozenset[int]
    remaining_capacity: int | None

class LeaveStatus(StrEnum):
    LEFT = "left"
    NOT_PARTICIPANT = "not_participant"
    SESSION_FINISHED = "session_finished"

@dataclass(frozen=True, slots=True)
class LeaveResult:
    user_id: int
    status: LeaveStatus
    participants: frozenset[int]
    remaining_capacity: int | None
```

The two compact result types keep branch-specific typing without adding a public dataclass for
every normal outcome. `JOINED` and `LEFT` mean the membership change has been committed. A
capacity or lifecycle decision is represented by a result; persistence, fencing, programming,
and delivery failures remain exceptions or existing open results.

`ParticipantPolicy.limit` must be positive or `None`; `None` means unbounded. User IDs supplied
to membership operations and as the initial `actor_id` must be positive Discord IDs, and `bool`
is not accepted as an integer ID.

`Session` exposes:

```python
@property
def participant_policy(self) -> ParticipantPolicy: ...

@property
def participants(self) -> frozenset[int]: ...

@property
def participant_limit(self) -> int | None: ...

@property
def remaining_capacity(self) -> int | None: ...

def has_participant(self, user_id: int) -> bool: ...

async def join(self, user_id: int) -> JoinResult: ...
async def leave(self, user_id: int) -> LeaveResult: ...
```

`remaining_capacity` is `None` for an unbounded session and otherwise
`max(limit - len(participants), 0)`. Result objects contain immutable snapshots so callers can
render the outcome they received even if another operation changes the live session afterward.

No additional participant-policy field is added to `Screen`; its existing `policy` field carries
the nested `ParticipantPolicy`. `SessionRegistry.open`, `Screen.open`, and
`DurableSessionRuntime.open` continue to accept one `SessionPolicy`.

## Membership semantics

The opener is the initial participant when `actor_id` is present. An actorless session starts
empty. This is a semantic default, not an authorization decision; it simply makes the opener's
session presence explicit. A positive finite participant limit can therefore always admit its
opener.

Attachment actors remain operational attribution and do not join automatically. Opening a child
screen, inspecting a session, or pressing one of its controls is not evidence that the user
accepted membership. Applications call `join()` at the domain event that actually means “join”.

`join(user_id)` runs under the session lifecycle lock:

1. Return `SESSION_FINISHED` if the session is closed, finishing, or its durable claim is gone.
2. Return `ALREADY_JOINED` if the user is already present. This remains idempotent at capacity.
3. Return `LIMIT_REACHED` if adding the user would exceed the finite limit.
4. Stage and persist the candidate set for a durable session.
5. Publish the new immutable set and return `JOINED`.

`leave(user_id)` uses the same lock. It returns `SESSION_FINISHED` for a dead session,
`NOT_PARTICIPANT` when absent, and otherwise persists and publishes the set without that user.

Leaving the opener or the final participant is allowed. An empty session remains alive; the
application decides when a lobby or game is finished. Membership changes do not alter mount
access, finish mounts, or transfer membership between sessions.

`ProtectCrossUserAttachments` continues to consult both explicit participants and attachment
actors. Once joins are real, an opener cannot replace a session while a different explicit
participant remains in it. `Unprotected` continues to opt out.

## Durability

Membership and participant capacity are facts of the durable logical-session record, not
component state.

- `SessionSummary` carries `participant_limit` and the current immutable participant set.
- `DurableSessionRecord` carries the same values alongside the mount graph.
- The durable record protocol advances to version 2.
- Protocol 1 records decode with an unbounded participant policy and membership derived from the
  stored root actor.
- Legacy summary payloads without the new participant-policy field are treated as old records;
  their participant set is derived from `actor_id`, including the historical empty set written
  during initial durable opening.
- Current summaries and records carry the exact set and limit. Recovery validates that their IDs,
  keys, participant policies, and participant sets agree.
- The separately stored summary remains the remote-admission projection; the full record remains
  the recovery snapshot. Both must agree before a session is registered.
- The next successful checkpoint writes the current protocol and exact membership for a legacy
  record.

The active durable runtime owns the session under its distributed lease. A membership operation
stages its candidate set while holding the lifecycle lock and performs an immediate fenced
checkpoint before returning `JOINED` or `LEFT`. A checkpoint/fence failure restores the old
in-memory set and raises `ParticipantPersistenceError`.

Checkpoint writes for one durable session are serialized so a maintenance checkpoint cannot
overwrite a newer membership snapshot. Claim-loss handling must not recursively finish a session
while the membership operation holds the lifecycle lock; teardown is deferred until that lock is
released.

The initial durable summary must be written from the constructed session's actual summary rather
than from a pre-session summary, so the opener's initial membership is persisted correctly.

## Inspection and operations

`SessionInspection`, summaries, and devtools show:

- the explicit participant IDs or count;
- the finite or unbounded participant limit; and
- remaining capacity.

Existing participant lists now mean explicit membership rather than merely echoing the opener.
No devtools join/kick operation is added; operational mutation needs its own authorization and
audit design.

## Not included

- No invitation, role, team, ready-state, owner, ban, or participant metadata model.
- No framework-level authorization callback.
- No minimum participant count or automatic finish when empty.
- No automatic join from attachment, access, or interaction activity.
- No participant-specific mount access changes.
- No waiting queue when capacity is full.
- No transfer of membership between sessions.
- No database schema migration; only versioned durable JSON payloads change.

## Verification

- The opener is the sole initial participant; an actorless session starts empty; attachment actors
  never join implicitly.
- Join succeeds below capacity, is idempotent when present, and returns the typed full result at
  capacity.
- Leave succeeds, is idempotent when absent, permits the opener and final participant to leave,
  and never finishes the session.
- Join/leave after or during finish return `SESSION_FINISHED` without mutation.
- Concurrent joins for the final slot admit exactly one user under the lifecycle lock.
- Participant membership affects `ProtectCrossUserAttachments`; `Unprotected` still permits
  replacement.
- Unbounded and finite capacity properties report correctly.
- Durable join/leave checkpoint before returning, survive recovery, and roll back in memory when
  persistence or fencing fails.
- Protocol 1 records recover with unbounded policy and root-actor membership; current records
  round-trip the exact set and limit.
- A recovered attachment actor remains attribution but is not promoted to membership.
- Inspection and devtools snapshots report the same membership and capacity as the live session.
- A test-only lobby fixture exercises caller-authorized admission without adding bot UX.
- Focused session, screen, durability codec/runtime, operations, public API, and typing tests
  pass; then run `just typecheck` and `git diff --check`.

## Implementation sequence

Each commit is independently valid and reviewable:

1. `sessions: model participant membership` — nested policy, membership state, result types,
   lifecycle semantics, and ordinary-session tests.
2. `durability: persist participant membership` — protocol 2 records, legacy decoding, atomic
   fenced membership checkpoints, rollback, and recovery tests.
3. `operations: inspect participant capacity` — inspection/devtools fields and public API tests.
4. `docs: revise session membership plan` — this decision record and status update.

## Status

Revised 2026-08-24. Designed; implementation not started. The first implementation must include
the framework lobby fixture and the durable recovery/race tests above before this plan is marked
complete.
