# PR 186 review follow-up plan

## Scope

Collect all 15 inline comments by `Glinte` on
[PR 186](https://github.com/redstone-squid/Redstone-Squid/pull/186),
“schematics: phase 0 stack (02/15)”. Collected on 2026-11-09 against PR head
`cce65cdb10f6ed028307f01705b716f4fddb619d`. Comments span 2026-10-31 through 2026-11-09.

The paginated GitHub endpoints for inline comments, general conversation comments,
and reviews were exhausted. There are no general conversation comments by `Glinte`.
The sole [review summary](https://github.com/redstone-squid/Redstone-Squid/pull/186#pullrequestreview-5724756118)
by `Glinte` has an empty body and adds no request.

The inventory below preserves every inline comment verbatim. All work packages
have a completed implementation or explicit design disposition below.

GitHub recheck on 2026-11-17: PR 186 is merged at the same published head;
all 15 threads remain unresolved, with no outdated anchors. Follow-up commits are
local on master; no GitHub replies, resolutions, or pushes were made.

## Implementation audit — 2026-11-17

P1 is satisfied: the backend receives raw request options plus the parsed query;
the redundant separate offset argument is gone. Keeping raw query text alongside
the parsed expression supports lexical/semantic ranking. Search service tests pass.

P3 is satisfied by `MessageSnapshot` and `to_message_snapshot`; observations now
use that name throughout the application and Discord adapters.

P4 is satisfied: a Discord NotFound suppresses the recorded post and marks its
message deleted. Forbidden and unreachable channels do not assert deletion.
Suppression survives later reconciliations. Keep `partial(self._remove, post)`:
`run_all` accepts zero-argument factories so it can acquire capacity before
starting each operation. Removing that binding would break its callable contract.
Reconciler tests cover suppression and repeated cleanup.

P5 is satisfied in `c5d706296`: the renderer explicitly implements `PostRenderer`;
`StarboardEntryKey` validates and encodes the board/message pair at the shared
boundary, and native channel types provide NSFW checks, including threads.
Resource-key and channel-safety tests passed.

P6 is satisfied: option parsing is imported at module scope. Keep the publisher
as the wizard's transport adapter: the application service creates the durable
session; publication attaches a Discord message and reconciliation owns its card.
`PollPublicationError` retains retry state so failed attachment reuses a returned
message and never creates a second session. Publisher and wizard tests pass.

P2 is satisfied in `d67b4d696`: provider modules own source definitions; deployment composition
selects provider groups and accepts additional sources without a central ID list.
The service resolves a source, checks visibility and context, bounds provider work,
and ranks candidates; composed providers preserve their ranking but cannot exceed
the requested limit. `MAX_QUERY_SUGGESTIONS` is the shared bounded-query ceiling;
full enumeration remains exempt. Discord separately enforces its own 25 choices.

Suggestion requests reuse `Actor`; no separate viewer identity is needed.
Commands already declare `autocomplete=suggests("source")` beside their parameters,
which makes the binding readable where the parameter is defined. The unused
`autocompletes` decorator was removed.

Keep Python taxonomy ordering to make revisions independent of database collation.
A deterministic 2,000-row benchmark took 0.517 ms unsorted and 0.276 ms after
emulating database lexical ordering. Saving 0.241 ms on an occasional cached load
does not justify an additional database sort that still cannot replace casefold
ordering. Both inputs produced the same final order. Registry, ranking, visibility,
service/Discord limits, form-option compatibility, and command binding tests pass.

## Validation

Focused search, publisher, wizard, reconciliation, resource-key, and channel
safety tests pass. Final local suite: 8,982 passed, 20 opt-in cases skipped.
Whole-project Pyrefly reports zero errors. Commit hooks format/lint changed files.

## Original comments

### 1. Comment 4433154892 (P1)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433154892).
Original path: `squid/core/search/application/services.py`, line 35.

> why are we passing the same information in request into two other arguments

### 2. Comment 4433227882 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433227882).
Original path: `squid/core/suggestions/application/services.py`, line 1.

> This seem to work but it is very confusing

### 3. Comment 4433228733 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433228733).
Original path: `squid/core/suggestions/domain/models.py`, line 12.

> but we are not discord specific, aso this desnt seem like a hard cap despite the name

### 4. Comment 4433230613 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433230613).
Original path: `squid/core/suggestions/domain/models.py`, line 50.

> do we really need another dataclass for a User but slightly different

### 5. Comment 4433252707 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433252707).
Original path: `squid/core/suggestions/infrastructure/catalogue.py`, line 63.

> I feel like we shouldn't register centrally

### 6. Comment 4433268217 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433268217).
Original path: `squid/bot/utils/autocomplete.py`, line 87.

> I really wish the API isn't so awkward to read, `@autocompletes(build_id="builds")` is succinct but not really natural

### 7. Comment 4447080538 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4447080538).
Original path: `squid/core/suggestions/infrastructure/providers/taxonomy.py`, line 138.

> I wonder if we should sort once in the db then once in python, to speed up the sorting on the python side

### 8. Comment 4472426273 (P3)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4472426273).
Original path: `squid/bot/message_adapter.py`, line 9.

> I really don't like the name MessageFact

### 9. Comment 4514430934 (P4)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4514430934).
Original path: `squid/bot/posts/reconciler.py`, line 65.

> should we store something about this information though

### 10. Comment 4514564480 (P4)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4514564480).
Original path: `squid/bot/posts/reconciler.py`, line 115.

> why is this partial needed?

### 11. Comment 4516078629 (P5)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4516078629).
Original path: `squid/bot/posts/starboard_renderer.py`, line 24.

> why not subclass the protocol/ABC

### 12. Comment 4516098072 (P5)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4516098072).
Original path: `squid/bot/posts/starboard_renderer.py`, line 44.

> the resource_kind resource_key seems like an implicit and highly coupled protocol

### 13. Comment 4516107122 (P5)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4516107122).
Original path: `squid/bot/posts/starboard_renderer.py`, line 99.

> what is this crap

### 14. Comment 4516255992 (P6)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4516255992).
Original path: `squid/bot/voting/publisher.py`, line 50.

> inline import

### 15. Comment 4516285229 (P6)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4516285229).
Original path: `squid/bot/voting/publisher.py`, line 1.

> very weird design
