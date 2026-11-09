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

### P3 Typed error-report service access

Comment 3 targets the nested `getattr` chain in `squid/api/errors.py::_capture`.
That chain remains in the current checkout.

- [ ] Inspect application initialization and existing typed runtime accessors.
  Use one explicit optional boundary for an app without a runtime, then typed
  access to services and error reporting. Avoid replacing the chain with unchecked
  casts or a second untyped lookup helper.
- [ ] Keep error capture usable when dependency resolution fails. Retain the
  protection that reporting failures cannot prevent the problem-detail response.
- [ ] Move the local observability import to module scope if dependencies permit;
  resolve any real cycle at its owning boundary.

**Acceptance:** tests cover a wired application, an application without reporting,
and reporting failure; each still returns the expected HTTP problem document.
Successful capture retains correlation metadata and the buffered log tail.

### P4 Account-linking flow

Comment 4 targets `VerifyCog.link` at the reviewed commit. That method read the
account, reserved a code, checked conflicts, requested consent, linked identities,
released unused reservations, and rendered the result. The current command opens
`AccountWorkspace`, whose `_link` method owns the linking interaction.

- [ ] Compare the reviewed method with `AccountWorkspace._link`, the account
  service, consent callbacks, and presentation helpers. Record which concerns
  the move has already addressed before planning further extraction.
- [ ] Make the normal reserve → check → consent → redeem flow easy to follow,
  with a clear owner for reservation cleanup. Keep presentation in the bot and
  account/link rules in the application service where appropriate.
- [ ] Preserve validation before consent, no account creation for an invalid code
  or declined consent, and release of unused reservations on conflict, timeout,
  abandonment, cancellation, or failure. Review asynchronous cleanup and duplicate
  callbacks explicitly; successful redemption must not be undone by cleanup.

**Acceptance:** focused linking/consent tests cover success, invalid code, conflict,
declined or expired consent, cancellation, and failure during redemption. Confirm
credit attribution and user-facing results remain correct.

## Execution and validation

Audit each work package against the implementation branch before editing. Settle
P2's identity contract before changing P1's actor construction if those types are
affected. Otherwise implement the packages independently in small, reviewable commits.

For implementation changes, run focused tests with `--no-cov` during development
and after the final edit, format and lint affected code, run `just typecheck`, and
run `git diff --check`. This plan collects feedback; it does not implement fixes
or change GitHub threads.

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
