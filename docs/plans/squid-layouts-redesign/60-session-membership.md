# 60 — Session membership: participants and per-session capacity

## Problem

`SessionPolicy` controls how many logical sessions may occupy one `SessionKey`. It does not
control how many users may participate in one session. Games, lobbies, collaborative editors,
and review rooms therefore have no framework operation for admission:

```text
SessionPolicy      = how many sessions may occupy this key?
ParticipantPolicy  = how many users may join this session?
```

`Session.participants` already appears in summaries and replacement protection, but today it is
initialized only from the root `actor_id` and never changes. Attachment actors are tracked
separately. Durable summaries serialize the participant set, then recovered `Session`
construction resets it from `actor_id`; that dormant mismatch becomes a real data-loss bug as
soon as membership can change.

Applications can keep their own set in component state, but then admission races with concurrent
clicks, replacement protection cannot see it, devtools report the wrong users, and a recovered
durable lobby forgets its members. Membership belongs to the logical session, not to any one
mount in its tree.

## Decision

Make membership a serialized operation on `Session`, governed by an immutable
`ParticipantPolicy` separate from `SessionPolicy`:

```python
mount = sessions.defaults.mount(Lobby(...), access=Owner(interaction.user.id))
opened = await sessions.open(
    mount,
    destination,
    key=SessionKey.guild("game", guild.id),
    participant_policy=ParticipantPolicy(limit=10),
    actor_id=interaction.user.id,
)

result = await opened.session.join(other_user_id)
left = await opened.session.leave(other_user_id)
```

Normal admission outcomes are typed values rather than exceptions. Operational failures—store
loss, programming errors, and Discord delivery failures—remain exceptions or existing open
results rather than being disguised as capacity decisions.

## Public API

`squid_layouts.discord.sessions` adds:

```python
@dataclass(frozen=True, slots=True)
class ParticipantPolicy:
    limit: int | None = None

@dataclass(frozen=True, slots=True)
class Joined:
    user_id: int
    participants: frozenset[int]

@dataclass(frozen=True, slots=True)
class AlreadyJoined:
    user_id: int
    participants: frozenset[int]

@dataclass(frozen=True, slots=True)
class ParticipantLimitReached:
    user_id: int
    limit: int
    participants: frozenset[int]

@dataclass(frozen=True, slots=True)
class ParticipantSessionFinished:
    user_id: int

type JoinResult = Joined | AlreadyJoined | ParticipantLimitReached | ParticipantSessionFinished

@dataclass(frozen=True, slots=True)
class Left:
    user_id: int
    participants: frozenset[int]

@dataclass(frozen=True, slots=True)
class NotParticipant:
    user_id: int
    participants: frozenset[int]

type LeaveResult = Left | NotParticipant | ParticipantSessionFinished
```

`ParticipantPolicy.limit` must be positive or `None`; `None` means unbounded. There is no minimum
in v1. A session may be empty, its opener may leave, and becoming empty does not finish it.

`SessionRegistry.open`, `Screen.open`, and `DurableSessionRuntime.open` accept:

```python
participant_policy: ParticipantPolicy = ParticipantPolicy()
```

`Screen` gains a `participant_policy` field because it is reusable per-screen opening policy;
this is an extension of the shipped value, not a second `SessionSpec` abstraction. A per-call
override follows the same precedence as other `Screen.open` overrides only if the existing typed
options surface can express it cleanly; otherwise the screen field is fixed and callers needing a
different policy call `SessionRegistry.open` directly.

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
`max(limit - len(participants), 0)`. Public integer inputs reject `bool` and non-positive Discord
IDs with `ValueError`; this matches session identity being Discord-user membership rather than an
arbitrary application key.

## Membership semantics

The root `actor_id`, when present, is the initial participant. Because a finite limit is always
positive, opening can always admit that one user. `actor_id=None` starts empty.

Attachment actors remain operational attribution and do **not** join automatically. Opening a
child screen, inspecting a session, or pressing one of its controls is not evidence that the user
accepted membership. Applications call `join()` at the domain event that actually means “join.”

`join(user_id)` under the session lifecycle lock performs, in order:

1. Return `ParticipantSessionFinished` if the session is closed, finishing, or its root is
   finished.
2. Return `AlreadyJoined` if the user is present; this succeeds idempotently even when the session
   is currently at capacity.
3. Return `ParticipantLimitReached` if adding one would exceed the finite limit.
4. Persist the candidate membership when the session is durable.
5. Publish the new immutable set and return `Joined`.

`leave(user_id)` uses the same lock, returns `ParticipantSessionFinished` for a dead session,
`NotParticipant` when absent, and otherwise persists then publishes the set without that user.
Neither operation finishes mounts, changes access policy, or sends a Discord response.

The participant policy is capacity only. It does not answer who is allowed to join. The caller,
route middleware, or application service owns invitation, ban, guild, payment, and game-state
authorization before invoking `join()`.

`ProtectCrossUserAttachments` already consults both `SessionSummary.participants` and
`attachment_actors`; no new replacement policy is needed. Once joins are real, an opener cannot
replace a session while a different explicit participant remains in it. `Unprotected` continues
to opt out.

## Durability

Membership and capacity are part of the durable logical-session record, not component state.

- `SessionSummary` gains `participant_limit`; `participants` remains the immutable current set.
- `DurableSessionRecord` and its codec carry `participant_limit` and `participants`.
- The durable session record protocol increments. The decoder accepts protocol 1 records as
  `participant_limit=None` with participants derived from the stored root actor, then writes the
  current protocol on the next checkpoint.
- The separately stored summary payload also carries `participant_limit`; its existing missing
  `participants` compatibility remains an empty/default decode only for genuinely old records.
- Recovery initializes `Session._participants` from the decoded summary/record, never by
  recomputing it from `actor_id` when durable membership is available.
- Summary and record validation requires matching ids, keys, participant policy, and participant
  sets so a torn store cannot quietly recover contradictory membership.

A durable join or leave is successful only after a fenced checkpoint stores the candidate set.
The operation stages the new set while holding the lifecycle lock; on checkpoint failure it keeps
the old in-memory set, updates durability health through the existing runtime path, and raises a
new `ParticipantPersistenceError`. Thus `Joined` and `Left` mean the membership will survive an
immediate process loss. Remote admission inspection sees the updated summary after the same
checkpoint.

The active durable runtime already owns the session under a distributed lease, so the local
lifecycle lock is the only in-process admission lock required. A lost fence makes persistence
fail rather than allowing two processes to admit independently.

## Inspection and operations

`SessionSummary`, `SessionInspection`, and devtools show the participant count, finite/unbounded
limit, and remaining capacity. Existing participant lists now mean explicit membership rather
than merely echoing the opener. No devtools join/kick operation is added in v1; operational
mutation needs an authorization and audit design of its own.

## Not included

- No invitation, role, team, ready-state, owner, or ban model.
- No minimum participant count or automatic finish when empty.
- No automatic join from attachment, access, or interaction activity.
- No participant-specific mount access changes.
- No waiting queue when capacity is full.
- No transfer of membership between sessions.
- No participant metadata; applications keep game-specific data in their own durable component or
  application store.

## Verification

- The root actor is the sole initial participant; an actorless session starts empty and attachment
  actors never join implicitly.
- Join succeeds below capacity, is idempotent when already joined, and returns the typed full result
  at capacity.
- Leave succeeds, is idempotent when absent, permits the opener and final participant to leave, and
  never finishes the session.
- Join/leave after or during finish return `ParticipantSessionFinished` without mutation.
- Concurrent joins for the final slot admit exactly one user under the lifecycle lock.
- Participant summaries immediately affect `ProtectCrossUserAttachments`; `Unprotected` still
  permits replacement.
- Unbounded and finite capacity properties report correctly.
- Durable join/leave checkpoint before returning, survive recovery, and roll back in memory when
  persistence or fencing fails.
- Protocol 1 durable records recover with unbounded policy and their root actor as the participant;
  current records round-trip the exact set and limit.
- A recovered attachment actor remains attribution but is not promoted to membership.
- Inspection and devtools snapshots report the same membership and capacity as the live session.
- Focused session, durability codec/runtime, operations, Screen, public API, and typing tests pass;
  then run `just typecheck` and `git diff --check`.

## Status

Designed. Depends on no other new plan; plan 61 may use it for future lobby-like role workflows
but does not require it.
