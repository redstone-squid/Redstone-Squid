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

The inventory below preserves every inline comment verbatim, with its original
file, line, and link. The work packages interpret those comments using their PR
diff context. All tasks await an implementation audit: unchecked items do not
claim the current checkout still has the problem, or that a GitHub thread is
unresolved. Questions remain decisions to investigate rather than predetermined
implementation requirements.

## Follow-up work

### P1 Search request arguments

- [ ] Review `SearchService.search` and the backend contract. The reviewed call
  passes `request`, a parsed query, and `offset=request.offset`; remove redundant
  inputs or make the distinction between raw and parsed input explicit.
- [ ] Verify query parsing and pagination behavior after any contract change.

Comment: 1.

### P2 Suggestion service and autocomplete API

- [ ] Trace the suggestion service's ordinary request flow and simplify the parts
  that make it hard to follow. Explain provider selection, ranking, visibility,
  and limits where their responsibilities are defined.
- [ ] Separate Discord's response limit from transport-neutral suggestion policy.
  Decide whether `MAX_SUGGESTIONS` is a default or an enforced ceiling, then align
  its name, documentation, and behavior with that decision.
- [ ] Review whether `SuggestionViewer` needs a separate identity representation or
  can reuse an existing user/account abstraction without coupling to Discord.
- [ ] Move source declarations toward the modules that own them; decide how they
  are composed without requiring every source to be registered centrally.
- [ ] Revisit the autocomplete declaration API. Compare readable call sites with
  the reviewed `@autocompletes(build_id="builds")` form before choosing a replacement.
- [ ] Evaluate database ordering before Python taxonomy sorting. Measure the cost
  and preserve the intended final order; the comment proposes an investigation,
  not an unconditional extra sort.
- [ ] Validate source registration, visibility, transport-specific limits, ranking,
  and autocomplete bindings with focused tests after implementation.

Comments: 2–7.

### P3 Message terminology

- [ ] Choose a clearer name for `MessageFact` based on the information it carries
  and how observation/persistence uses it. Update the type, adapter function,
  callers, and relevant documentation consistently.

Comment: 8.

### P4 Reconciliation state and task calls

- [ ] Decide what should be recorded when a deleted post is deliberately not
  recreated. Inspect existing tombstone/suppression state before adding storage,
  and distinguish a missing message from evidence of deliberate moderator action.
- [ ] Review why `_remove_all` wraps `_remove` with `partial` for `run_all`. Check
  the task runner's callable contract and simplify it if the wrapper is unnecessary.
- [ ] Verify deletion suppression survives repeated reconciliation and that bulk
  removal retains its concurrency and failure behavior after any change.

Comments: 9–10.

### P5 Renderer contracts and channel safety

- [ ] Review explicit inheritance from `PostRenderer` or an appropriate ABC for
  `StarboardEntryRenderer`, including whether it improves contract checking.
- [ ] Make the relationship between `resource_kind` and `resource_key` explicit.
  Replace scattered assumptions about colon-separated starboard/message IDs with
  a typed representation or a shared, validated encoding boundary.
- [ ] Replace or justify `_unsafe_nsfw`'s dynamic
  `getattr(channel, "is_nsfw", lambda: False)()` calls using the actual supported
  channel types. Preserve the restriction on mirroring NSFW content into an
  unmarked destination, including thread/channel edge cases.
- [ ] Validate resource-key handling, renderer conformance, and channel safety
  with focused tests after implementation.

Comments: 11–13. The terse wording of comment 13 is interpreted from its diff
anchor in `_unsafe_nsfw`; it does not specify a preferred replacement.

### P6 Poll publisher design

- [ ] Remove the local import of `parse_option_lines` from `resolve_options`.
  Inspect the dependency direction between publisher and wizard and move shared
  parsing to an appropriate module if a module-level import would cause a cycle.
- [ ] Review the publisher facade as a whole: option parsing, palette lookup,
  poll creation, and Discord publication should have clear owners and a coherent
  interface for wizard callers. Record the design decision before restructuring.
- [ ] Verify option parsing and the wizard's create-and-publish flow after changes.

Comments: 14–15.

## Execution and validation

First compare each concern with the implementation branch and record whether it
still needs work. Implement independently valid changes in small commits; P2's
source API decisions should precede its call-site changes, and P5's resource
contract should be settled before changing callers in reconciliation.

For implementation changes, run the affected tests with `--no-cov`, format and
lint the changed code, run `just typecheck`, and run `git diff --check`. This file
only collects review feedback; it does not implement fixes or update GitHub threads.

## Original comments

### 1. Comment 4433154892 (P1)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433154892).
Original path: `squid/search/application/services.py`, line 35.

> why are we passing the same information in request into two other arguments

### 2. Comment 4433227882 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433227882).
Original path: `squid/suggestions/application/services.py`, line 1.

> This seem to work but it is very confusing

### 3. Comment 4433228733 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433228733).
Original path: `squid/suggestions/domain/models.py`, line 12.

> but we are not discord specific, aso this desnt seem like a hard cap despite the name

### 4. Comment 4433230613 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433230613).
Original path: `squid/suggestions/domain/models.py`, line 50.

> do we really need another dataclass for a User but slightly different

### 5. Comment 4433252707 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433252707).
Original path: `squid/suggestions/infrastructure/catalogue.py`, line 63.

> I feel like we shouldn't register centrally

### 6. Comment 4433268217 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4433268217).
Original path: `squid/bot/utils/autocomplete.py`, line 87.

> I really wish the API isn't so awkward to read, `@autocompletes(build_id="builds")` is succinct but not really natural

### 7. Comment 4447080538 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/186#discussion_r4447080538).
Original path: `squid/suggestions/infrastructure/providers/taxonomy.py`, line 138.

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
