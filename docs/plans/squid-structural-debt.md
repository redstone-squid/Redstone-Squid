# Structural debt in squid and its tests

Status: submission convergence implemented around the sanitizer boundary; remaining tracks are queued.
Reviewed: 2026-09-07.

The approved submission design and implementation record are in
[`submission-convergence.md`](submission-convergence.md). Its decisions supersede section 1
where the original review recommended preserving the deprecated endpoint.

## Objective

Contain the maintenance cost of new submission features, cross-feature persistence operations,
and test coverage. Prioritize shared behavior and clear ownership over splitting large files.
This plan records the structural review; each implementation milestone should begin by checking
its assumptions against the current code and existing completed plans.

## 1. Converge submission orchestration

### Evidence and growth risk

- `squid/bot/submission/submit.py` finalizes forms, persists builds, records analyses, and publishes voting UI.
- `squid/bot/submission/ingestion.py` separately prepares attachments, checks duplicates, persists builds,
  and records enrichment. Its failure-evidence persistence path documents possible operator recovery.
- `squid/submissions/application/finalization.py` provides durable finalization for synchronized drafts.
- At review time, `squid/api/v1/builds.py` retained a deprecated direct door-submission endpoint;
  it has now been removed with explicit approval.

These paths share build persistence but distribute preparation and recovery policy. New categories,
attachment types, and validation rules can multiply implementations and failure cases.

### Intended changes

- Extract shared preparation and finalization operations into the application layer. Keep transport
  input collection, download adapters, consent presentation, and response rendering in their transports.
- Define explicit outcomes for rejection, successful build creation, and pending or failed follow-up
  work. A failure after persistence must retain the build identity and must not invite duplicate creation.
- Reuse existing durable finalization, event, and reconciliation mechanisms for recoverable follow-up
  work. Inspect their current ownership before introducing any additional job type.
- Migrate Discord forms and inferred message bundles incrementally. Preserve inference's multi-build
  behavior and intentional differences in attachment selection and partial-failure handling.
- Remove the deprecated HTTP submission endpoint and coordinate draft/attempt contracts with consumers,
  as authorized during detailed planning.

### Acceptance

- Equivalent normalized inputs follow the same shared validation and persistence policy across callers.
- Tests cover failure and retry between creation, analysis attachment, and publication, including process
  interruption wherever durable recovery is promised. Retries do not create duplicate builds or posts.
- Existing Discord recovery behavior remains covered; OpenAPI verifies the deprecated endpoint is absent.
- Shared submission policy no longer requires matching edits in the form and inference transports.

### Completion

Completed 2026-09-07. API, manual Discord, inferred-message, and recalculation entry points now use
persisted drafts and shared preparation/finalization policy. Attempts retain immutable accepted inputs,
build/media/receipt/event commits are atomic, and Discord recovery reads durable state. The deprecated
endpoint, superseded source writer, and direct persistence backdoors are removed. Supplied schematics
remain private and pending until the upstream sanitizer gate in `submission-convergence.md` passes.

The checked API fake now includes every production service field, and transport tests fail on missing
collaborators. Submission application policy also no longer imports Pydantic; retained JSON encoding
and validation sit behind an infrastructure codec. The broader test-boundary cleanup in section 3
remains its own track.

## 2. Establish ownership for cross-feature persistence

### Evidence and growth risk

`squid/accounts/infrastructure/repository.py` combines routine account operations with knowledge of
notification source keys, delivery fencing, submission finalization payloads, permissions, and voting.
Infrastructure imports also run in both directions between accounts/notifications, builds/tags, and
media/submissions. These are feature dependencies, not proof of runtime import cycles or defects.
Current architecture tests chiefly constrain horizontal layers rather than these feature relationships.

### Intended changes

- Extract account merging as an explicit transactional operation behind the existing repository entry
  point. Move feature-specific merge rules into helpers owned by the relevant feature.
- Pass the existing database session into those helpers. Preserve one transaction, deterministic lock
  ordering, phase ordering, and rollback across all participating features.
- Centralize notification source-key construction and validation so creation and merging share the
  grammar, including accepted legacy record keys and their canonical replacements.
- Inventory cross-feature writes separately from read projections. Give each write an explicit owner;
  allow deliberate query projections to read across features without adding unnecessary service hops.
- Add targeted architecture rules for the resulting ownership boundaries. Avoid a blanket prohibition
  on cross-feature SQL or a generic merge-plugin framework.

### Acceptance

- Existing merge tests retain coverage for late-failure rollback, reversed concurrent merges, conflict
  collapse, legacy-key replay, and stale delivery completion.
- Every account reference has an explicit merge disposition, including references retained or deleted
  rather than reassigned. Check the disposition inventory against schema references where feasible.
- A feature's merge policy can change without editing its SQL and data grammar inside account CRUD.
- No asynchronous event-based replacement weakens the current atomic merge guarantee.

## 3. Refactor test boundaries alongside production boundaries

### Evidence and growth risk

`tests/unit/api/fakes.py` casts an object-typed `FakeApiServiceGraph` to `ApiServices`; at review time
the fake lacks the production `public_records` field. Several collaborators accept arbitrary method
names. Other tests inspect source text, private helper inventories, or exact explanatory prose, such
as the retained-SQL justification in `tests/unit/accounts/test_merge_phases.py`.

Unchecked doubles can hide interface drift while implementation-shape assertions make harmless
refactors costly. More tests built this way can increase maintenance without increasing confidence.

### Intended changes

- Replace the unchecked fake service graph with checked construction and explicit collaborator methods.
  Introduce narrow consumer protocols where they express a real capability boundary; use autospecced
  doubles where appropriate instead of creating an interface for every class.
- Make unexpected calls fail clearly. Keep generated-contract rejection stubs explicit about which
  operations and signatures they implement.
- Move wiring guarantees to behavioral tests and merge correctness to outcome/transaction tests.
  Keep architecture checks for dependency direction, task ownership, and public boundaries; stop pinning
  incidental source spelling or exact documentation text.
- Share test support when several tests need the same contract, without creating a second application
  framework inside fixtures. Integrate these changes into the submission and merge milestones.

### Acceptance

- Missing service fields and incompatible collaborator signatures are caught by construction/type checks.
- Tests fail when wiring or behavior is wrong, but survive private helper moves and equivalent syntax.
- Existing behavioral coverage is retained when source-shape assertions are removed.

## 4. Give build metadata explicit types and ownership

### Evidence and growth risk

`Info` in `squid/builds/domain/models.py` combines notes, server details, unresolved taxonomy,
duplicate evidence, attachment failures, and `submission_provenance: dict[str, Any]`. Multiple layers
mutate its nested structures. Each new key distributes more interpretation and compatibility policy.

### Intended changes

- Introduce typed provenance and enrichment values and explicit operations for adding or replacing
  evidence. Start with these growing structures rather than redesigning every build field.
- Decode and encode persisted metadata through one compatibility boundary. Preserve the existing JSON
  storage shape initially; do not require a database migration for the type extraction.
- Preserve unrelated and historical metadata on updates. Define handling for missing optional fields,
  unknown keys, and malformed historical values before replacing direct dictionary access.
- Migrate producers and consumers together, including submission flows and review-card rendering.

### Acceptance

- Historical payload fixtures and round-trip tests demonstrate storage compatibility and preservation
  of unrelated keys. Invalid evidence receives explicit handling rather than silently changing meaning.
- Provenance and enrichment updates no longer require callers to know nested persistence dictionaries.
- Rendering and finalization retain existing user-visible evidence.

## Sequencing and validation

1. Establish checked API test construction and behavioral characterization for submission recovery.
2. Extract shared submission operations, then migrate each caller in separate coherent milestones.
3. Centralize notification keys, extract transactional merge ownership, then enforce chosen boundaries.
4. Introduce typed metadata before adding further provenance/enrichment features. Pull its initial
   extraction into submission work if needed to avoid introducing another dictionary-based interface.

Commit early and at each independently valid milestone, with concise imperative component-scoped
subjects and wrapped bodies explaining non-trivial changes. Keep behavior changes separate from
mechanical moves where possible. Update this plan with completed work and validation evidence.

For each milestone, run focused tests with `--no-cov`, then relevant architecture and integration tests.
Run `just typecheck` after the final code edits and read the complete result. Check `git diff --check`;
check Alembic heads when persistence is affected. Verify installed hooks before relying on them for
changed-file formatting and linting. Defer the full suite to CI unless central changes leave the blast
radius uncertain. Do not rerun unchanged passing checks without a reason.

This plan itself changes documentation only. The preceding review ran schema-checker and merge-phase
tests: 24 passed and 1 PostgreSQL-only ARRAY case skipped. That result is not validation of these
unimplemented refactors.

## Boundaries and follow-ups

- Keep the modular monolith, existing PostgreSQL transactions, and anyio task ownership model.
- Do not replace the already shared queue claim protocol with a new generalized workflow framework.
- Configuration projection cleanup, legacy schema-checker retirement/repair, and deterministic
  verification-code tests remain smaller follow-ups from the initial review, outside these four tracks.
- No production behavior change, schema migration, or refactor is authorized by writing this plan alone.
