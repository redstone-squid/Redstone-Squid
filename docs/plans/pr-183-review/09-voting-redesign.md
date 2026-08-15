# PR #183 Review: Voting Redesign

## Findings

- The branch now has application-owned vote sessions, stable option identifiers, typed choices, API reads/writes, and a
  `VoteRejection` alias. This addresses part of the request for polls beyond Discord and the request for an enum-like
  rejection contract, although `VoteRejection` should become a `StrEnum` if callers keep branching on it.
- Generic polls are still effectively Discord-owned: creation takes a guild, the wizard passes a whole `VoteCog`, and
  publication creates one Discord message inline. `VoteTarget` also conflates build and message-deletion targets.
- The Discord commands and UI remain awkward (`/vote poll`, manual `emoji | label` parsing, textual visibility input),
  with authorization and visibility behavior implicit in event handlers. Missing messages and failed reaction setup do
  not produce an administrator-visible recovery path.
- `GenericVoteSession.from_id()` returning `None` is appropriate for a read lookup, but publish flows should treat a
  missing just-created session as an invariant error. A weak reference to the cog would not fix the ownership problem;
  narrow dependencies are the useful change.
- The sentinel thresholds `32767/-32768` remain for generic polls even though generic sessions never threshold-close.
  Current multi-message and API work has made several older repository concerns partly stale, but the persistence model
  still needs explicit optional threshold/target contracts.

## Subplans

1. **Domain and persistence model**
   - Split target data into typed build, message-deletion, and poll metadata rather than nullable `VoteTarget` fields.
   - Make threshold fields optional for non-threshold polls and migrate sentinel values to `NULL`; reject invalid
     kind/target/threshold combinations at the domain and database boundaries.
   - Promote rejection/status/visibility values used across layers to `StrEnum`s and remove casts/assertions that only
     compensate for untyped persistence strings.
2. **Transport-independent polls**
   - Add an application command that creates a poll draft/session independently of publication locations, then attach
     one or more presentation messages separately. Do not require a Discord guild merely to own the poll.
   - Keep per-location emoji aliases in the Discord adapter; application votes continue to address stable option IDs.
   - Define idempotent publish/attach and recovery operations so a database session can survive a failed message send.
3. **Discord command and UI redesign**
   - Replace the current command names with a poll-oriented group and use select/components for visibility, duration,
     and options wherever Discord supports them; keep parsing as an explicit fallback, not the primary interface.
   - Give the modal a small application/publisher facade instead of `VoteCog`. Report publish, reaction, missing-message,
     and permission failures to the initiating user and a configured moderator channel.
   - Move creator/staff, eligibility, anonymity, and reaction-removal policy into named application decisions; render
     typed rejection reasons into localized user messages.
4. **Discord-state recovery**
   - Treat message deletion, reaction clear, emoji removal, permission loss, and inaccessible guild/channel state as
     explicit reconciliation inputs. Recreate only safe presentation state and never infer discarded anonymous ballots.
   - Route due-close and refresh work through durable events plus the owned background supervisor.
5. **Test cleanup**
   - Extract repository fixtures/builders from the oversized integration cases. Keep direct SQL only where it asserts a
     database constraint or concurrency contract that the repository API cannot expose.

## Tests

- Domain matrix for every vote kind, target, optional threshold, visibility, and typed rejection.
- Repository migration, concurrent cast/close, arbitrary option ID (including `"generic"`), and multi-location tests.
- Application tests for create-before-publish, idempotent attach, partial publication failure, and authorization policy.
- Discord adapter tests for component validation, localized errors, deleted messages/reactions, and restart recovery.
- API compatibility tests for ballot privacy and stable option-ID writes.
