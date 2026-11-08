# Advanced Alchemy repository assessment

Reviewed: 2026-11-01. Scope: persistence adapters in this codebase.

Follow-up, 2026-11-02: the settings primary-key defect described below is fixed by configuring
`id_attribute = "server_id"`. PostgreSQL regression coverage now exercises existing-row settings
updates, locale changes and clearing, and guild removal/rejoining. The original assessment and
pre-fix reproducer are retained below as historical evidence; the broader adoption proposal remains open.

Keep Advanced Alchemy and expand it selectively during the planned move to application-owned units of
work. Do not schedule a blanket migration or require every repository to inherit from it. Audit the
existing helpers first: settings updates already have a reproducible primary-key configuration defect.

This is a design assessment, not an implementation decision. Runtime code and dependencies are unchanged.

## Existing foundation

- `uv.lock` already selects Advanced Alchemy 1.12.0 and SQLAlchemy 2.1.6.
- [BaseAsyncRepository](../../squid/persistence/repository.py) already subclasses
  `SQLAlchemyAsyncRepository`. Versions, settings, and voting use private model repositories built on it.
- The [shared entity base](../../squid/persistence/base.py) already includes Advanced Alchemy's
  `BasicAttributes`, alongside `MappedAsDataclass`. Adoption does not require replacing our models.
- The [database engine](../../squid/persistence/engine.py) owns the session factories and uses
  `expire_on_commit=False`. Most repositories open short sessions and return loaded, detached entities.
- [Entity unification](completed/merge_domain_and_persistence_models.md) is implemented; its second phase,
  application-owned units of work, has not started. The current contract is documented in
  [Entities and persistence](../models.md).

Advanced Alchemy therefore adds no new dependency decision here. The question is whether broader use
of its repository API improves the persistence code enough to justify migration and semantic review.

## What the competing proposals establish

The strongest case for broader adoption is a consistent set of typed model operations backed by a
library we already use. Unified entities and existing sessions make incremental composition practical.
Upstream can maintain ordinary bulk operations and loading machinery; our code can concentrate on
domain-specific persistence. Neither a framework switch nor a schema migration is needed.

The strongest objection is limited benefit from a separate retrofit. Diagnostics already saves a row
with a transaction context and `session.add`. API-key creation could replace explicit commit/refresh
calls, but still needs construction, a domain-facing adapter, and its guarded usage update. Adding model
helper classes and configuration can consume the apparent reduction. Large repositories contain
authorization predicates, transactions, projections, and state machines that remain our responsibility.

Independent adoption, skeptical, and capability reviews converged after challenging those examples:
evaluate helpers in the first unit-of-work slice, rather than migrating every current adapter first.
The repeated session-factory contexts disappear when transaction ownership moves; Advanced Alchemy
alone does not remove them. Treat it as an optional implementation tool within a consistent transaction
architecture. Do not measure success by the proportion of repositories inheriting from one base class.

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
- [ClaimedRowQueue](../../squid/persistence/queue.py) explicitly supports caller-owned sessions after
  an earlier implementation committed a transaction it did not own. Its
  [integration tests](../../tests/integration/persistence/test_claimed_row_queue.py) cover rollback.
- [Notification claims](../../squid/notifications/infrastructure/repository.py) return recipient
  details in one statement, with a [query-count test](../../tests/integration/notifications/test_repository.py).
  Loading entities and then updating each one would lose that guarantee.

## Current upstream capabilities

Advanced Alchemy supports existing sessions, custom SQLAlchemy expressions, model loading options,
bulk operations, and a query repository for custom statements. Specialized SQL is compatible with using
the library; complexity alone is not a reason to reject it.
[Repository overview](https://advanced-alchemy.litestar.dev/latest/usage/repositories/basics.html).

Version 1.12.0 adds native PostgreSQL conflict handling to eligible `upsert_many` calls. It falls back
when the conflict key or operation is unsuitable. That improvement does not make every method named
"upsert" an atomic replacement for our existing conflict statements.
[Release notes](https://advanced-alchemy.litestar.dev/latest/changelog.html).

Repository defaults include `auto_commit=False`, `auto_expunge=False`, `auto_refresh=True`, and
`wrap_exceptions=True`. Migration must explicitly review refreshes, exception types, and transaction
ownership. Existing settings/version calls that opt into automatic commits are not a template for
multi-step application transactions.
[Repository API](https://advanced-alchemy.litestar.dev/latest/reference/repository.html).

Inspection of the installed 1.12.0 implementation adds several practical qualifications:

- `get_or_upsert` and single-object `upsert` still look up before writing. They cannot mechanically
  replace [idempotency reservation](../../squid/idempotency/infrastructure/repository.py), whose
  `ON CONFLICT DO NOTHING RETURNING` decides the winning caller in the database.
- `get_or_upsert(with_for_update=True)` does not pass that lock to its initial lookup; it passes it
  to the later refresh. Audit each method's behavior rather than inferring locking from its signature.
- `add` flushes immediately even with `auto_commit=False`. Default refresh adds another operation.
  A unit of work must account for flush timing, generated values, and constraint-error timing.
- The default window-count path in `get_many_and_count` returns zero when the fetched page is empty.
  Our search has explicit beyond-the-last-page total semantics; retain its query or choose a separate
  count and test the result.
- Exception wrapping changes SQLAlchemy exceptions into Advanced Alchemy exceptions. Preserve existing
  catches with `wrap_exceptions=False` where appropriate, or translate deliberately at the adapter boundary.

These observations concern the installed version's source and database-free behavioral probes. They
are not measurements of PostgreSQL throughput or a claim that every library operation has these limitations.

## Audit the existing settings helper first

`_ServerSettingModelRepository` inherits `id_attribute = "id"`, while `ServerSetting` has the single
primary key `server_id`. Calling `update` raises `RepositoryError`, caused by
`AttributeError: 'ServerSetting' object has no attribute 'id'`, before executing SQL. This affects the
existing-row branches of `set`, `set_locale`, and `on_guild_remove`; successful insertion or lookup does
not exercise the failing path.

The failure reproduces with the locked dependencies and no running database:

```python
import anyio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from squid.settings.infrastructure.repository import _ServerSettingModelRepository
from squid.settings.models import ServerSetting


async def main() -> None:
    engine = create_async_engine("postgresql+asyncpg://unused:unused@localhost/unused")
    try:
        async with AsyncSession(engine) as session:
            repository = _ServerSettingModelRepository(session=session)
            await repository.update(ServerSetting(server_id=123))
    finally:
        await engine.dispose()


anyio.run(main, backend="asyncio")
```

Set the helper's `id_attribute` to `"server_id"` in a separate fix and cover insert-then-update,
locale updates, and guild removal against PostgreSQL. Review concurrent settings creation separately:
the current lookup-then-insert paths also need an explicit conflict policy. This is an integration
configuration defect, not evidence that Advanced Alchemy cannot support the model. No runtime fix is
included in this assessment.

## Proposed sequence and adoption criteria

1. Fix and test the existing settings configuration. Audit the three current helpers for identifier,
   commit, refresh, exception, and conflict behavior before treating them as migration examples.
2. Build on the completed [entity unification](completed/merge_domain_and_persistence_models.md), starting with a
   small write workflow such as version creation. Establish an explicit terminal commit, rollback on
   exceptions/cancellation or uncommitted exit, and one session per operation. Retain the existing
   engine, mapped entities, ports, and transport schemas.
3. Compare direct SQLAlchemy and an Advanced Alchemy helper within that same transaction design.
   Diagnostics lists/counts or API-key add/lookup are bounded additional candidates if the first slice
   does not exercise enough reusable operations. Do not roll out a separate repository-wide conversion.
4. Adopt helpers where they demonstrably remove repeated code or make loading/query contracts clearer.
   Preserve specialized methods and SQL in the same session. Keep generic CRUD internal so callers
   cannot bypass revision guards or other domain operations through an inherited `update` or `delete`.
5. Expand opportunistically as workflows migrate or need substantive changes. If helpers merely wrap
   an already short statement, keep the direct implementation. Framework services, middleware, model
   bases, caching, and database portability are separate decisions with no demonstrated need here.

For the pilot, use `auto_commit=False` and `auto_expunge=False`; choose `auto_refresh` and exception
wrapping explicitly per operation. A retained attached entity can often be mutated and flushed by the
unit of work without a generic repository `update` at all. Keep generated-value loading deliberate.

Accept a migrated operation only when its tests establish:

- The same public return values, missing-row behavior, errors, ordering, and pagination totals.
- Correct primary keys, loaded relationships, generated values, and usable detached results.
- Whole-operation rollback, explicit commit, and session isolation; later aggregate slices must prove
  rollback across repositories and preserve audit/event writes in the same transaction.
- Real PostgreSQL concurrency behavior for any conflict, revision, reservation, or claim operation touched.
- No unnecessary queries or refreshes, using the existing `record_queries` test helper; preserve current
  query-count contracts and distinguish measured query counts from unmeasured latency claims.

There is no demonstrated project-wide performance win or quantified maintenance saving yet. The pilot
should establish those benefits before committing to a broader migration; it can also conclude that
direct SQLAlchemy remains clearer for a particular operation.

## Verification of this assessment

The locked environment imports Advanced Alchemy 1.12.0 with SQLAlchemy 2.1.6 on Python 3.14.8.
The settings failure was reproduced with a real `AsyncSession` and guards proving that execution,
flush, and refresh were never called. Separate database-free probes checked add defaults, initial
lookup locking, and empty window-count results.

Project-wide `just typecheck` passed with zero errors, using a writable temporary uv cache. The focused
entity architecture, settings service, version service, and API-key repository checks passed: 230 tests.
Those checks do not cover the settings update defect. No PostgreSQL migration or performance experiment
was run, and no production code or dependency was changed. Installed commit hooks cover Markdown
whitespace; `git diff --check` is also required before each commit.
