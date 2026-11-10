# Tiny-helper audit

Audit baseline: `fa7d274c5`, 2026-11-10. Review in progress.

This audit follows [the small-helper guidance](../code-documentation-style.md#small-helpers).
The question is whether an abstraction helps its callers, not whether it is short.
Recommendations are recorded before implementation so unrelated changes can be reviewed separately.

## Scope and method

Inventory tracked source, then inspect definitions, callers, imports, registrations, exports, and test patch targets.
Python screening includes functions, methods, and nested functions; a second pass considers larger operations
whose ownership is wrong even though they exceed the small-function threshold. Historical migrations and
completed design spikes are not cleanup targets. Generated clients must be changed through their generator.

The initial inventory contains 1,678 Python files and 9,965 functions with at most five executable AST statements
(excluding test functions and empty bodies, but including methods, overloads, and protocol declarations).
Those are screening candidates, not findings. Identifier occurrence counts only prioritize inspection;
they are not resolved call graphs and cannot establish that a public API is unused.

## Issue classes

| Class | Decision |
| --- | --- |
| Behavior detached from its owner | Move to the existing type when its state and invariants determine the operation. |
| Detached alternate constructor | Put conversion on the destination type, provided this preserves dependency direction. |
| Forwarding layer | Inline when it supplies no policy, lifecycle, type, or callable boundary. |
| One-use accessor or formatting fragment | Prefer the expression or a named local when the caller becomes clearer. |
| Dead helper | Remove only after checking registrations, exports, string lookups, and patch targets. |
| Duplicate policy | Share within the owning subsystem; do not merge superficially similar rules from unrelated domains. |
| Hidden mutation or resource acquisition | Make the effect and ownership visible; preserve transactional and cleanup behavior. |
| Misleading validation or fallback | Verify the contract against invalid inputs and distinguish coercion from validation. |
| Excessive parameter plumbing | Move behavior with the state it repeatedly receives rather than creating more wrappers. |
| Over-generalized abstraction | Narrow the interface to actual callers; retain meaningful extension points. |
| Test indirection | Keep scenario setup and semantic assertions; inline trivial one-off setup and remove stale helpers. |
| Required callable or type boundary | Keep callbacks, dependency providers, decorators, factories, overloads, and type guards. |

## Confirmed findings

### H01: HTTP caller behavior belongs on Caller

`squid/api/security.py`: `require_consented_account` (210), `credential_allows` (94),
`subject_for` (99), and `caller_allows` (75) operate on `Caller` but live beside it.
Routes repeatedly import the type and its operations separately. Move these to
`Caller.require_consented_account()`, `Caller.credential_allows(node)`,
`Caller.permission_subject()`, and `Caller.allows(permissions, node)`.

Keep `current_caller`, `requires`, `current_browser_account_id`, and `consented_account_id` as FastAPI
integration functions. Preserve account-less service credential behavior, anonymous checks, guild-less
permission subjects, consent error context, and authentication-before-consent precedence.

### H02: Pagination conversion is detached from its response types

`squid/api/pagination.py`: `anchor` (99) and `render_page` (106) construct the adjacent `PageAnchor` and `Page`
types. Use response-owned `from_domain` constructors, consistent with the other API schemas. Keep optional
anchor handling explicit and preserve generic item conversion. Do not move HTTP policy in `resolve_selector`
into the shared pagination model.

### H03: Candidate construction is detached from Candidate

`squid/suggestions/application/matching.py`: `candidate` (48) builds `Suggestion` and then `Candidate`.
The conversion, label fallback, and matching terms belong together on a `Candidate` alternate constructor.
Preserve the current treatment of an empty label when moving it. Keep the ranking and matching algorithms.

### H04: Schematic bootstrap has a one-use constructor wrapper

`squid/bootstrap.py`: `create_schematic_analyzer` (126) only returns `QueuedSchematicAnalyzer(jobs, artifacts, config)`.
Its sole caller is `create_schematic_service` (149). Construct it there and retain the explanation that native
execution belongs to the worker. The factory does not choose an implementation or manage a resource.

### H05: Build persistence has an extra forwarding method

`squid/builds/infrastructure/repository.py`: `_update_existing` (510) obtains `self._session()` and calls
`_update(session, build)`. The existing-build branch at 474 can do that directly. Keep `_update`, which owns
substantial persistence behavior, and preserve session evaluation order.

### H06: Vote reactions wrap an already named consent operation

`squid/bot/voting/reactions.py`: `_consented_account_id` (272) only awaits
`self._voters.consented_account_id(discord_id)`. Its caller at 132 can call the resolver directly.
The wrapper does not translate errors, cache, or enforce another rule.

### H07: Unreferenced application helpers remain

`collect_lookback` in `squid/bot/submission/message_context.py:137` and `utcnow` in `squid/core/time.py:6`
have no references in the tracked Python source. Check all tracked text and dynamic entry points before
removal. The latter also returns a formatted string despite its clock-like name; do not promote it into a
replacement time API.

## Validation

Pending completion of the audit. No production behavior has been changed.
