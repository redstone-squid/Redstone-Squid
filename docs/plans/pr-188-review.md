# PR 188 review follow-up plan

## Scope

Collect all three inline comments by `Glinte` on
[PR 188](https://github.com/redstone-squid/Redstone-Squid/pull/188),
“schematics: phase 0 stack (04/15)”. Collected on 2026-11-11 against PR head
`154780efc4b16f7604ff49a5d3ce0ac32731d0f1`.

The paginated inline-comment, conversation-comment, and review endpoints were
exhausted. The ordinary inline-comment endpoint returned no comments; all three
were retrieved from the comments endpoint for the pending
[review](https://github.com/redstone-squid/Redstone-Squid/pull/188#pullrequestreview-5833041179).
Its summary is empty, and there are no conversation comments. Original wording
and anchors are preserved below.

## Completion audit — 2026-11-17

All three requests are addressed in the implementation checkout. GitHub review
state and propagation to the published stack are separate from this audit.

### P1 Profile contracts

Traced profile validation, repositories, `AccountProfileEdit`, API schemas, and
`AccountWorkspace`. Retain `ProfileUpdate`: UNSET means omitted, while None clears
text. Retain `CreatorProfileRecord` and `PublicCreatorProfile`: the former carries
private identity data, and the latter is the visibility-filtered public contract.
Combining these types would erase patch semantics or expose private information.
`CreditedAlias` supplies build counts without conflating display names with credit.

In `2562d7013`, merge ticket constants and `MergePreview` now live with account ownership models,
not profile editing. The profile module explains its remaining representations
once at the top. Validation and public presentation stay together because they
operate on the same small profile contract; separate forwarding services would
add indirection without changing ownership.

### P2 Account operations

Keep `AccountService` as the transaction owner for identity lookup, verification,
linking, merge tickets, and alias claims. Its current entry points already express
the separate operations; splitting them into delegating facades would obscure the
shared unit of work. Transport adapters authenticate the acting account. Profile
writes now belong to `AccountProfileEdit` through `EditRunner`, which rechecks
ownership, permissions, consent, and revisions in the write transaction.

Linking reserves and checks the code before the bot requests consent, then redeems
it through the service. Reservation cleanup belongs to `AccountWorkspace`; see
PR 187 P4 for cancellation and duplicate-callback fixes. Merge preview is read-only;
completion consumes the ticket and merges identities and credit atomically. Alias
review resolves the claim and reconciles credit through repository operations.
External name lookup remains outside write transactions. No repository transaction
boundaries were changed by this review cleanup.

### P3 Caller consent

`Caller.require_consented_account()` owns the check. No free-function imports
remain. Dependency adapters retain browser-only versus account-owned credential
restrictions and supply dependency injection rather than duplicating validation.

Validation: account unit tests, consent dependencies, profile routes, account panel,
and consent-gate tests passed. Coverage includes patch normalization and clearing,
hidden profiles and private avatars, redirects, verification failure and lockout,
reservation release, and single-use merge behavior. Repository code is unchanged;
no new database migration or repository integration run was needed.

## Original comments

### 1. Comment 4518184058 (P1)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/188#discussion_r4518184058).
Original path: `squid/accounts/domain/profiles.py`, file-level comment.

> so much verbosity and calsses that look kind of the same here, dont really like how large this file is

### 2. Comment 4526456696 (P2)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/188#discussion_r4526456696).
Original path: `squid/accounts/application/services.py`, file-level comment.

> confusing

### 3. Comment 4526918240 (P3)

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/188#discussion_r4526918240).
Original path: `squid/api/security.py`, anchored to the definition of
`require_consented_account(caller: Caller) -> int` (line numbers unavailable).

> should be a method
