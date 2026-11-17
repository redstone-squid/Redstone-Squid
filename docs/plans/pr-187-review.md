# PR 187 review follow-up plan

## Scope

Collect all four inline comments by `Glinte` on
[PR 187](https://github.com/redstone-squid/Redstone-Squid/pull/187),
“schematics: phase 0 stack (03/15)”. Collected on 2026-11-09 against PR head
`d455a1c7e31edbce5f4e16d2ffbbbcad910a9a6f`.

The paginated inline-comment, conversation-comment, and review endpoints were
exhausted. There are no conversation comments by `Glinte`; the sole
[review summary](https://github.com/redstone-squid/Redstone-Squid/pull/187#pullrequestreview-5831031408)
has an empty body. All four comments are preserved verbatim below.

The plan uses the review anchors and a preliminary inspection of the current
`schematics-phase-0-stack-23` checkout. Unchecked tasks describe follow-up work,
not GitHub thread status. The broad design comments require investigation before
choosing a replacement; moving code alone does not establish that they are resolved.

## Implementation progress — 2026-11-17

P3 is satisfied in `637e9a9c7`: one optional runtime lookup is followed by typed
`ApplicationRuntime`/`ApiServices` access. Observability imports are module-level;
reporting failures remain contained. API application tests passed.

P4 is satisfied in `63af9dd89`: the workspace owns reservation cleanup, while the account service
owns redemption and credit reconciliation. Reserve and conflict checks precede
consent; declined consent never creates an account. Settlement is serialized so
repeated answers and late abandonment cannot redeem twice or release a consumed
reservation. Cleanup is shielded and catches cancellation during redemption as
well as ordinary failures. Account panel, consent, and service tests passed,
including new cancellation/failure and duplicate-answer regressions.

P1 and P2 remain open. Shared `Actor` has replaced several former identity shapes,
but the REST lifecycle and remaining wrappers still require a complete audit.

## Follow-up work

### P1 Discord REST responsibilities

Comment 1 concerns the whole `squid/voting/infrastructure/discord_rest.py` file.
The current module combines a gateway-less discord.py client, lazy login and URL
rebasing, and cached member lookup with voting capability resolution.

- [ ] Trace bootstrap, shutdown, and adapter callers. Separate reusable REST
  client lifecycle from voting-specific member resolution where this gives each
  responsibility a clear owner. Keep one shared client per process for rate limits.
- [ ] Review the HTTPClient subclass and replacement of the client's connection
  state. Isolate and explain any necessary discord.py internals; evaluate simpler
  supported construction before introducing another abstraction.
- [ ] Make login, failed-login retry, concurrent first requests, cancellation,
  and close behavior explicit. Preserve configurable API URLs and bounded
  rate-limit waits.
- [ ] Verify member-cache expiry and the distinction between absent members,
  unreadable guilds, and transient failures. Preserve interactive errors and the
  background refresh behavior that retains cached weights when lookup fails.

**Acceptance:** focused client/resolver tests cover lifecycle and lookup outcomes;
callers can follow client ownership without tracing voting policy code.

### P2 Access-control actor types

Comment 2 targets `BuildEditor`, originally a subject plus optional Discord ID.
It now wraps only `Subject`. Related representations include permission-admin
`Actor`, `VoteActor`, `ReactionActor`, `SuggestionViewer`, and
`AuthenticatedSubmissionActor`.

- [ ] Inventory these types and their callers: distinguish authenticated identity,
  permission subject, resource ownership, and resolved capabilities. Identify
  duplicate wrappers and conversions without assuming every actor means the same thing.
- [ ] Decide whether build editing can accept `Subject` directly, and remove
  redundant wrappers where they add no contract. Keep domain-specific facts where
  policies actually need them; avoid one actor with many optional fields.
- [ ] Coordinate the suggestion identity decision with P2 of
  [the PR 186 plan](pr-186-review.md), so the two efforts use the same conclusion.
- [ ] Update transport adapters and application callers together. Preserve
  authorization rechecks inside write transactions and the distinction between
  account IDs and Discord IDs.

**Acceptance:** build-edit, submission, and affected permission tests preserve
allowed and denied outcomes, including missing identity and scoped permissions;
remaining actor types have a clear reason to exist.

## Validation

API application, account service, account panel, and consent tests pass, including
redemption cancellation and repeated callbacks. Whole-project Pyrefly passes.
Remaining REST and actor work must retain transactional authorization checks and
shared client ownership. GitHub threads were not changed.

## Original comments

### 1. Comment 4516694585 (P1)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/187#discussion_r4516694585).
Original path: `squid/voting/infrastructure/discord_rest.py`, file-level comment.

> seems like a mess

### 2. Comment 4516718316 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/187#discussion_r4516718316).
Original path: `squid/builds/application/services.py`, lines 27–37.

> there seems to be so fucking many access control "Actors" dataclass

### 3. Comment 4517590609 (P3)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/187#discussion_r4517590609).
Original path: `squid/api/errors.py`, lines 75–76.

> bruh why are we using so many getattr

### 4. Comment 4517760338 (P4)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/187#discussion_r4517760338).
Original path: `squid/bot/verify.py`, line 43 (`VerifyCog.link`).

> this function seems a bit messy still
