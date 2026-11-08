# Unify persisted entities, then introduce units of work

## Status

Both phases are implemented. Persisted entities are unified, and application operations now own business transactions
through units of work. Database listeners and queue-health monitoring remain infrastructure connection owners.
See [Entities and persistence](../../models.md) for the current model boundary.

## Goal and boundaries

Use one SQLAlchemy model for each persisted entity, including larger entities such as Build and Account. Move business behavior onto those models and remove duplicate domain classes and mechanical conversion code.

Unified entities live under squid/<context>/models.py, using a models/ package where the context already needs several modules. Retain context ownership, repositories, application services, and explicit public schemas.

Deliver two independently valid phases:

1. Unify models while preserving current transaction boundaries.
2. Move transaction ownership into application-level units of work.

Preserve public API shapes, stored payload formats, and business behavior. Phase 1 preserves structural DDL; phase 2
adds a preview cleanup claim so object deletion can run outside a database transaction. The earlier field-binding
framework is outside this plan.

## Phase 1: Unify entities

### Establish the shared model boundary

- Move mapped classes into their context’s model namespace, including currently ORM-only tables. Update model registration, relationship references, imports, fixtures, and architecture tests.
- Allow domain policies, application services, and transports to reference shared entities. Keep SQL queries, sessions, engines, and persistence adapters behind repositories.
- Shared models may depend on SQLAlchemy, the declarative base, and pure value types; they must not import services, repositories, transports, or native schematic engines.
- Keep pure enums and values independent of model exports to prevent circular imports. Remove temporary compatibility exports before completing the phase.

### Merge entity representations

| Area | Unification |
|---|---|
| Messages, posts, diagnostics | Replace stored-record wrappers with mapped entities; retain observation inputs and routing projections. |
| Accounts and authentication | Unify accounts, identities, profiles, aliases, claims, stored credentials, device enrollments, challenges, and sessions. Retain authenticated-principal projections and one-time-secret results. |
| Tags, permissions, notifications | Unify definitions, persisted assignments, roles, grants, subscriptions, and inbox records. Retain compiled permission rules, filters, delivery claims, and computed preference projections. |
| Builds and starboard | Move entity behavior onto mapped models, including category subclasses and owned records. Preserve explicit loading of related and derived facts. |
| Remaining contexts | Move existing mappings into the shared namespace and remove table-shaped entity wrappers. Preserve values, aggregate snapshots, job claims, and public projections. |

Specifically retain BuildDraft, submission snapshots and versioned payloads, vote-session snapshots, schematic analyses, search results, record calculations, and MinecraftVersion. These represent incomplete state, captured state, computation, or value semantics rather than a second live entity.

Prefer existing domain entity names for merged classes; retain existing names for mapped-only classes. Update callers instead of maintaining parallel SQL* and domain names.

### Preserve behavior while removing conversion

- Reuse the existing MappedAsDataclass base. Preserve useful constructors, factories, properties, defaults, and business methods; move validation out of deleted mappers.
- Use the existing enum column adapters so loaded entities expose actual enums. Preserve typed JSON and embedded values through properties or column adapters without changing stored representations.
- Make entity equality identity-based. Retain structural equality for value objects and snapshots; update callers that previously relied on frozen entity equality or hashing.
- Replace asdict(), replace(), and deepcopy() usage on migrated entities with explicit serialization or purpose-specific preview values. In particular, build revision previews must remain isolated from persistence.
- Treat JSON and array edits as explicit replacement operations so nested changes are persisted reliably.
- Preserve repository-owned sessions and explicit writes during this phase. Return entities with their documented data loaded and detached; retain expire_on_commit=False. Ordinary field copying between entity classes disappears, while relationship synchronization and guarded writes remain explicit.
- Configure relationships to reject implicit SQL and select loading options in repository queries. Keep cross-context enrichment batched; do not replace it with an unrestricted relationship graph.
- Preserve build revision checks, immutable provenance, identity normalization, consent checks, ownership rules, and database event behavior.

Implement in dependency order: shared infrastructure and simple records; accounts/authentication and tags; permissions/notifications; builds/starboard; remaining wrappers and cleanup.

## Phase 2: Application-owned units of work

Introduce a framework-independent UnitOfWork protocol and factory, implemented with one SQLAlchemy session and
transaction. Expose typed, session-bound repositories and explicit terminal commit(). Leaving without committing rolls back;
repositories flush when they need generated values or constraint checks. Each service declares the repositories it needs
through a protocol extending the shared transaction lifecycle. The factory remains a callable type alias.

The application API becomes:

```python
async with self._uow_factory() as uow:
    build = await uow.builds.get_for_update(build_id)
    authorize_edit(actor, build)
    patch.apply(build)
    await uow.commit()
```

- Repository methods query, add, delete, and flush; they do not open independent sessions or commit. Mutating an attached entity persists through the unit of work.
- The outer application operation owns the transaction. Internal operations receive the same unit of work or its repositories explicitly. Avoid ambient sessions and nested independent commits.
- Commit is explicit and terminal. Leaving without committing discards writes; exceptions and cancellation roll back and close the session. Returned entities must be loaded and detached, or converted into existing projections before leaving the scope.
- Preserve database locks, optimistic versions, claim tokens, audit writes, and event writes. Move submission finalization and account merging onto the shared transaction mechanism without splitting their existing atomic operations.
- Keep database transactions short. External API calls, inference, rendering, uploads, and user interaction happen outside them; revalidate relevant authority and revisions before committing.
- Worker claim, processing, and completion remain separate steps with separate units of work. Retain existing durable queues for post-commit work.
- Never share a unit of work across concurrent tasks. Independent work gets independent instances.
- Replace long-lived edit leases with draft/patch previews; reload, authorize, check the expected revision, and apply the patch inside a short commit operation.
- Update composition roots and service test doubles. Remove obsolete repository session factories, automatic commits, and parallel *_in_session interfaces as each workflow migrates.

Start with one simple write service, then build edits, submission finalization, account merging, and the remaining application workflows. Infrastructure-only connection lifetimes, such as database listeners, remain separately owned.

## Phase 2 outcome

All business repositories are bound to one application-owned scope. This includes authentication and account merging,
build edits, voting and records, submissions and finalization, notifications, media and schematics, search and
suggestions, and worker claim/completion operations. The shared queue requires the caller's session. Architecture
checks prevent repository-owned commits and new session-factory owners.

Build edits prepare detached previews, then reload, authorize, check the revision, and save inside a short transaction.
Submission finalization and account merging keep their dependent writes atomic. Record publication commits all
requested scopes together. External storage, inference, embedding, and rendering work happens between transactions;
completion rechecks the relevant authority or claim. Concurrent suggestion providers use independent read scopes.

Preview cleanup uses a durable claim token to exclude publishers while bytes are deleted outside the database.
Revision `b3d7f0a5c8e1` adds the claim timestamp, token, and paired-null constraint in one reversible migration.

## Verification and completion

- Establish schema and public-contract baselines before moving classes. Require unchanged tables, columns, constraints, defaults, and polymorphic identities. Collect documentation changes in one migration and run the schema-drift integration checks.
- Test construction without a database, validation, enum loading, typed JSON, and insertion/update/reload for each migrated context.
- Verify returned entities remain usable after session closure and that property access issues no unexpected SQL. Preserve existing batching and query-count guarantees.
- Test preview isolation, stale edits, rollback, cancellation, generated IDs, immutable fields, and atomic audit/event writes.
- For phase two, prove multi-repository rollback, explicit-commit behavior, session isolation, claim fencing, and absence of repository-owned commits.
- Preserve HTTP, Discord, queued-payload, credential-disclosure, and snapshot behavior through existing contract and integration tests.
- Run focused tests with --no-cov per slice, formatting/linting, project typechecking, architecture checks, and git diff --check. Run the full suite and PostgreSQL integration suite at each phase gate because the impact is broad.
- Update architecture guidance, migration documentation, and the build-attribute guide to reflect the unified model boundary. Phase one is complete only when duplicate entity classes and mechanical entity mappers are gone; phase two is complete when application operations consistently own business transactions.

## Phase 1 verification

The model move preserves structural DDL and indexes. Revision `a2c6e9f4b7d0` collects the documentation changes
in one reversible migration: four table comments and seventeen column comments. It was the phase-one migration head.

- Unit, architecture, and workspace package suite: 5,798 passed, 1 skipped.
- PostgreSQL integration suite: 532 passed, 4 skipped, 5 existing failures reproduced on pre-refactor revision `3082bbd0f`.
- Project Pyrefly check: no errors. Formatting, linting, and `git diff --check` pass.
- Clean migration upgrade, schema drift, downgrade, and re-upgrade checks pass.

The initial run found five existing integration failures:

- The disposable API stack uses a Docker build path without BuildKit support for `RUN --mount`.
- The scoped storage query leaves PostgreSQL parameter `$5` without a type.
- The installed OpenTelemetry instrumentor rejects SQLAlchemy 2.1.6.
- The historical schematic-render migration fixture runs an event trigger against the old submitter column.
- The historical nullable-vote-threshold fixture runs a queue trigger before `available_at` exists.

The four integration skips require ffmpeg/ffprobe; the unit skip covers PostgreSQL arrays on SQLite.

Follow-up fixes resolve all five failures. The final focused run passed 83 storage, tracing, migration, and unit
tests; the Docker lifecycle test and 23 environment unit tests also passed. The Docker harness now uses BuildKit,
closes controller connections, compares host addresses without network masks, and authenticates with a valid seeded
service key. Historical schema tests isolate unrelated delivery triggers; applied migration files remain unchanged.
Pyrefly, formatting, linting, and `git diff --check` pass.

## Phase 2 verification

- Unit, architecture, and workspace suite: 5,812 passed, 2 skipped. The typing contract test passed when rerun
  with the installed checker on PATH; the remaining skip covers PostgreSQL arrays on SQLite.
- PostgreSQL integration suite: 574 passed, 4 skipped because ffmpeg/ffprobe are unavailable.
- Final focused checks after import cleanup pass. Project Pyrefly reports no errors; formatting, linting, and
  `git diff --check` pass.
- Clean migration upgrade, schema drift, downgrade, and re-upgrade checks pass. `b3d7f0a5c8e1` is the single head.
