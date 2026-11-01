# Advanced Alchemy repository assessment

Reviewed: 2026-11-01. Scope: persistence adapters in this codebase.

Expand Advanced Alchemy selectively where its model operations remove repeated work. Do not require
every repository to inherit from it or replace specialized SQL with generic CRUD. Coordinate expansion
with the existing plan for application-owned units of work so repositories do not change ownership twice.

This is a design assessment, not an implementation decision. Runtime code and dependencies are unchanged.

## Existing foundation

- `uv.lock` already selects Advanced Alchemy 1.12.0 and SQLAlchemy 2.1.6.
- [BaseAsyncRepository](../../squid/persistence/repository.py) already subclasses
  `SQLAlchemyAsyncRepository`. Versions, settings, and voting use private model repositories built on it.
- The [shared entity base](../../squid/persistence/base.py) already includes Advanced Alchemy's
  `BasicAttributes`, alongside `MappedAsDataclass`. Adoption does not require replacing our models.
- The [database engine](../../squid/persistence/engine.py) owns the session factories and uses
  `expire_on_commit=False`. Most repositories open short sessions and return loaded, detached entities.
- [Entity unification](merge_domain_and_persistence_models.md) is implemented; its second phase,
  application-owned units of work, has not started. The current contract is documented in
  [Entities and persistence](../models.md).

Advanced Alchemy therefore adds no new dependency decision here. The question is whether broader use
of its repository API improves the persistence code enough to justify migration and semantic review.

## Where broader use fits

| Area | Assessment |
|---|---|
| Versions, settings, voting model helpers | Keep the existing integration; review transaction policy as workflows move to units of work. |
| Diagnostics and API-key entity operations | Best bounded candidates for a comparison: ordinary insertion, lookup, counts, and ordered lists. |
| Tags, posts, messages, permissions, accounts | Consider individual ordinary entity operations; retain conditional writes, conflict policies, and projections. |
| Builds, account merging, submission finalization | Preserve aggregate transaction boundaries, explicit loading, and revision guards. Helpers may operate inside the same session. |
| Events, sync, schematic jobs, media jobs, notifications | Preserve queue claims, leases, fencing, and acknowledgement behavior; reuse the existing queue abstractions. |
| Search, suggestions, records | Preserve query-specific SQL and result shapes. A generic query wrapper needs a concrete benefit. |
| CLI and Minecraft authorization, browser sessions, idempotency | Preserve atomic consumption, reservation, expiry, and conditional state transitions. |

These are method-level choices. A repository can benefit from an Advanced Alchemy helper for a lookup
while keeping an explicit PostgreSQL statement for its write.

## Semantics that must survive

- [Message upserts](../../squid/messages/infrastructure/repository.py) preserve the original
  `observed_at`; deletion records only the first deletion time.
- [Post writes](../../squid/posts/infrastructure/repository.py) advance revisions only when newer,
  and distinguish a duplicate message ID from a conflicting live post.
- [API-key usage updates](../../squid/auth/infrastructure/repository.py) include the throttling predicate
  in the update itself. Replacing that with read, mutate, and generic update would change concurrency behavior.
- [OAuth state consumption](../../squid/auth/infrastructure/sessions.py) uses `DELETE ... RETURNING`
  so a credential is consumed once.
- [Diagnostics lookup](../../squid/diagnostics/infrastructure/repository.py) chooses the newest match
  when several reports share a short reference. A uniqueness-assuming lookup would change that contract.
- [Account merging](../../squid/accounts/infrastructure/repository.py) and
  [submission finalization](../../squid/submissions/infrastructure/finalization_repository.py) coordinate
  multiple writes in a transaction. Library helpers must not commit individual steps.

## Current upstream capabilities

Advanced Alchemy supports existing sessions, custom SQLAlchemy expressions, model loading options,
bulk operations, and a query repository for custom statements. Specialized SQL is compatible with using
the library; complexity alone is not a reason to reject it.
[Repository overview](https://advanced-alchemy.litestar.dev/latest/usage/repositories/basics.html).

Version 1.12.0 adds native PostgreSQL conflict handling to eligible `upsert_many` calls. It falls back
when the conflict key or operation is unsuitable. That improvement does not make every method named
"upsert" an atomic replacement for our existing conflict statements.
[Release notes](https://advanced-alchemy.litestar.dev/latest/changelog.html#id1).

Repository defaults include `auto_commit=False`, `auto_expunge=False`, `auto_refresh=True`, and
`wrap_exceptions=True`. Migration must explicitly review refreshes, exception types, and transaction
ownership. Existing settings/version calls that opt into automatic commits are not a template for
multi-step application transactions.
[Repository API](https://advanced-alchemy.litestar.dev/latest/reference/repository.html).
