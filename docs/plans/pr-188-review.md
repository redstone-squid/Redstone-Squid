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

The plan includes a preliminary inspection of `schematics-phase-0-stack-23`.
Unchecked items describe follow-up work, not GitHub thread status. Recheck the
implementation before editing: the third request already appears implemented.

## Follow-up work

### P1 Profile types and module responsibilities

Comment 1 concerns the whole `squid/accounts/domain/profiles.py` file. It currently
combines text/link validation, partial updates, internal profile records, public
views, avatar presentation, and merge-preview types and constants.

- [ ] Trace the profile types through repositories, application services, API
  schemas, and bot presentation. Identify which similar shapes represent real
  boundaries and which only add copying or naming overhead.
- [ ] Simplify redundant representations and conversions. Preserve the distinction
  between omitted update fields (`UNSET`), explicit clearing, internal records,
  and public output; merging those contracts could change edits or expose private data.
- [ ] Group validation, public presentation, and merge-specific definitions by
  responsibility where that makes callers easier to follow. Choose boundaries
  after reading their uses, rather than creating one module per dataclass.
- [ ] Trim repetitive explanations while retaining the reasons for visibility
  filtering, URL restrictions, and patch semantics. Explain the remaining types'
  relationships once near their definitions.

**Acceptance:** a reader can follow profile editing and public presentation without
guessing why each representation exists. Focused profile validation and presentation
tests preserve normalization, link validation, omitted-versus-cleared fields, hidden
profiles, private identities and avatars, and merge redirects. Profile edits must
remain separate from creator-credit attribution.

### P2 Account service flow and ownership

Comment 2 concerns the whole `squid/accounts/application/services.py` file and does
not prescribe a replacement. `AccountService` currently coordinates account lookup,
consent, verification and linking, merges, profiles, identity refresh, and alias claims.

- [ ] Trace representative callers and repository operations. Describe the normal
  flow for linking, merging, editing profiles, and resolving alias claims, including
  who authenticates the actor and who commits related changes.
- [ ] Identify the specific branches, duplicate orchestration, or mixed responsibilities
  that obscure those flows. Decide whether clearer methods and grouping suffice or
  whether cohesive services would help; avoid replacing one large file with a facade
  and many forwarding wrappers.
- [ ] Coordinate profile ownership with P1 and linking responsibilities with P4 of
  [the PR 187 plan](pr-187-review.md). Update bootstrap and transport callers together
  if service boundaries change.
- [ ] Preserve transaction boundaries and build-edit history when account operations
  change credit. Keep consent checks, verification lockouts, reservation expiry and
  release, conflict handling, recent merge proofs, and single-use merge tickets intact.
  External username lookups should remain outside write transactions.

**Acceptance:** the service's public entry points and their owners are easy to find,
and each main flow can be read without following redundant delegation. Focused
account service tests and affected repository integration tests preserve success,
failure, rollback, and concurrent-use behavior for the operations changed.

### P3 Consent check as a caller method

Comment 3 targets the former module-level `require_consented_account(caller)`.
The current checkout already defines `Caller.require_consented_account()` in
`squid/api/security.py`; routes and dependency adapters call it. Existing coverage
in `tests/unit/api/test_consent_dependency.py` exercises the method.

- [ ] Verify the implementation branch retains this method and has no stale imports
  or calls to the former free function. Keep FastAPI dependency adapters where they
  provide dependency injection rather than duplicating the check.
- [ ] Run the consent dependency tests. Confirm callers without an account raise
  `AuthenticationError`, pending consent raises `ConsentRequiredError` with consent
  links, and accepted account-owned credentials return their account ID. Preserve
  the browser-only restriction in `current_browser_account_id`.
- [ ] Record this request as satisfied if those checks pass; make further changes
  only if the audit finds a gap.

**Acceptance:** consent validation belongs to `Caller`, its callers use that method,
and the existing credential and error behavior remains covered.

## Execution and validation

Verify P3 first, then settle P1's type boundaries before changing profile operations
in P2. Implement independently valid changes in small commits. Run affected tests
with `--no-cov` during development and after the final edit, format and lint changed
code, run `just typecheck`, and run `git diff --check`.

This file records feedback and follow-up work; it does not implement the refactors
or change the pending review or its threads.

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
