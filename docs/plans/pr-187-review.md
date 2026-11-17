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

All four requests have a local implementation below. GitHub was rechecked on
2026-11-17: PR 187 is merged at the same head; all four threads remain unresolved
(one anchor is outdated). Follow-ups are local on master; no replies, resolutions,
or pushes were made.

## Completion audit — 2026-11-17

P3 is satisfied in `637e9a9c7` and `8df7ef991`: one optional runtime lookup is
followed by typed `ApplicationRuntime[ApiServices]` access. Runtime imports stay
annotation-only so exception handling does not load the application graph.
Observability imports are module-level;
reporting failures remain contained. API application tests passed.

P4 is satisfied in `63af9dd89`: the workspace owns reservation cleanup, while the account service
owns redemption and credit reconciliation. Reserve and conflict checks precede
consent; declined consent never creates an account. Settlement is serialized so
repeated answers and late abandonment cannot redeem twice or release a consumed
reservation. Cleanup is shielded and catches cancellation during redemption as
well as ordinary failures. Account panel, consent, and service tests passed,
including new cancellation/failure and duplicate-answer regressions.

P1 is satisfied in `54564e6f4`: reusable REST lifecycle belongs to
`squid/discord_rest.py`; the voter adapter owns membership lookup and capability
resolution. Bootstrap creates one cached client and registers its close callback.
The HTTP subclass is necessary because discord.py has no per-client Route base
and its public login fetches unused application details. The isolated adapter
updates both Client and ConnectionState HTTP references so returned models share
rate-limit state. Concurrent login, retry after failure/cancellation, shutdown,
TTL expiry, absent membership, unreadable guilds, and transient failures are tested.
Membership caches contain Discord facts, with account ownership and permissions
resolved again so identity merges and permission changes cannot reuse stale authority.

P2 is satisfied in `8918bdf90`: build authorization accepts `Actor` directly,
with history source supplied by the edit operation. `Subject`, permission-admin
`Actor`, `VoteActor`, `ReactionActor`, `SuggestionViewer`, and
`AuthenticatedSubmissionActor` have converged on `squid.core.actors.Actor`.
It carries trusted identity/context, with require methods for account, Discord ID,
and delegation rank. Capabilities are feature-specific resolved results; permission
checks still resolve current authority independently. Persistence/history IDs remain
distinct from account/Discord IDs. Transactional edit and recalculation authorization
checks remain in their write transactions. Build unit tests and PostgreSQL
recalculation tests pass; suggestions reuse the same identity contract (PR 186 P2).

## Validation

API application, account service, account panel, and consent tests pass, including
redemption cancellation and repeated callbacks. Whole-project Pyrefly passes.
REST lifecycle, voter cache, build authorization, and PostgreSQL recalculation
tests also pass. GitHub threads were not changed.

Final whole-checkout validation: 8,982 local-suite tests passed, with 20 opt-in
cases skipped. Whole-project Pyrefly reports zero errors.

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
