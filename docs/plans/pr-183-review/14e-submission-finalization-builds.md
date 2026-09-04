# PR #183 Review 14E: Submission Finalization and Builds

## Scope

This plan covers twenty threads in durable submission finalization, its PostgreSQL models/repository, canonical build
creation, and the focused build tests. It starts with a validated draft and attachment readiness from 14D/14F and
ends with one retry-safe build identity plus durable review/notification intents.

## Findings

### One service currently validates, resolves, normalizes, queues, and presents failures

`SubmissionFinalizationService.submit` is readable but owns five policies: manifest validation, sponsor resolution,
attachment readiness, taxonomy checks, and normalization. The worker then owns target write, failure classification,
state transition, and two best-effort publications. The long functions are symptoms of missing application values,
not a reason to create one class per `if` statement.

Split preparation into three cohesive collaborators:

- `SubmissionPreparation` combines validated answers with sponsor and attachment facts into either
  `PreparedSubmission` or typed `SubmissionIssue`s;
- `BuildSubmissionWriter` performs retry-safe canonical build creation;
- `FinalizationCoordinator` owns durable transitions and publishes outbox intents only after completion.

Validators remain pure functions grouped by the value they validate. Do not turn individual fields into handler
classes or inject the full service graph.

### Error and port names describe implementation mechanics

`ActionableSubmissionError`, `SubmissionTarget`, `SubmissionNotificationPort`, and `SubmissionReviewEventPort` do not
say which application action failed or what is published. Replace them with `BuildSubmissionRejectedError`,
`BuildSubmissionWriter`, `FinalizationNoticePublisher`, and `ReviewRequestPublisher`. Remove “without assuming a
transport” from docstrings; protocols state the required action and idempotency contract directly.

Expected owner-repair outcomes should be returned as typed issues from preparation/writer boundaries. Exceptions are
reserved for violated invariants and infrastructure failure. This aligns finalization with the repository-wide
structured error pattern.

### The stored payload codec is persistence code by accident

`PostgresFinalizationJobRepository` manually encodes and decodes a large nested `NormalizedSubmission`, including
duplicated optional/string/integer helpers. Pydantic is appropriate for this versioned JSON boundary, not for
database query orchestration.

Move a strict `FinalizationPayloadV2` codec into a serialization module. It converts to/from domain values, rejects
unknown fields, carries an explicit schema version, and produces canonical JSON for the existing digest. The
repository treats the encoded object as opaque apart from digest/version checks. Migration coverage must decode
every retained v1 payload before switching writers to v2; old workers must either understand v2 or be drained before
the cutover.

### Result metadata has no second target

`SubmissionTargetResult` stores `target_key` and arbitrary JSON `provenance`, and the result table persists both. The
only production target is canonical build creation, while the source draft, owner, sponsor, schema revision, and
artifacts already live in the retained job/build records.

Reduce the result to `FinalizedBuild(build_id)`. Any target-specific diagnostic facts belong in structured logs or a
named audit table, not an unvalidated JSON bag. Remove `target_key` and `provenance` through a migration after proving
no external reader uses them.

### Status conversion and model boilerplate remain

Map `SubmissionFinalizationJob.status` as `FinalizationJobStatus` instead of scattering `.value`. Rewrite long SQL
check expressions as named triple-quoted constants so state branches are reviewable. Do not introduce a local
`UUIDv7AuditBase`: the repository-wide UUIDv7 primary-key migration is already explicitly deferred in
`schema-audit.md`/`TODO.md`, and a finalization-only base would make that migration harder. A small timestamp mixin is
acceptable only if at least three current models share identical semantics and Alembic emits no accidental changes.

### Legacy build entry points cannot simply be deleted yet

`DoorSubmissionInput` is still used by the direct HTTP build-write route; `BuildDraft` remains the active Discord and
message-inference editing model. Treat the two comments separately:

- retire `DoorSubmissionInput` and `BuildService.submit_door` only after the HTTP route creates/finalizes a revisioned
  submission draft;
- retain `BuildDraft` until the Discord/inference producers use the revisioned draft model, then remove it in the same
  migration—not in this plan by assertion alone.

Delete unit tests that merely restate removed mapping code. Preserve tests for canonical build invariants and
idempotent source-draft creation.

### Integration setup is dominated by raw SQL

`test_submission_targets.py` uses direct SQL for routine setup and assertions across accounts, drafts, schematics,
permissions, and merges. Extract SQLAlchemy factories in `tests/support/` and use mapped models/repositories for
normal setup. Keep direct SQL only for constraints, legacy migration rows, and database behaviors that no public
repository exposes; wrap each retained query in a named helper stating that reason.

## Planned work

1. **Inventory retained payload/result readers.** Prove whether any deployment or API reads `target_key`/`provenance`,
   enumerate payload schema versions, and pin v1 decode fixtures.
2. **Extract typed preparation.** Introduce the preparation result/issue union and pure grouped validators; keep
   current durable transitions unchanged.
3. **Rename writer/publication boundaries and errors.** Update composition and tests without changing persistence.
4. **Introduce the strict payload codec.** Dual-read v1/v2, write v2 only after worker compatibility, and retain
   canonical digest behavior.
5. **Simplify result persistence.** Migrate to build ID only, remove arbitrary target metadata, and make publication
   intents derive named facts from the job/build.
6. **Type job state and clarify constraints.** Map enums, name multiline state SQL, and keep repository transitions
   exhaustive.
7. **Retire the direct door command path.** Route HTTP creation through revisioned drafts/finalization, remove
   `DoorSubmissionInput`/`submit_door`, and delete only historical delegation tests.
8. **Modernize integration fixtures.** Replace routine SQL with typed factories; preserve explicit low-level
   constraint/concurrency/migration probes.

## Core interfaces

```python
@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    value: NormalizedSubmission


@dataclass(frozen=True, slots=True)
class PreparationRejected:
    issues: tuple[SubmissionAttentionIssue, ...]


type PreparationResult = PreparedSubmission | PreparationRejected


@dataclass(frozen=True, slots=True)
class FinalizedBuild:
    build_id: int


class BuildSubmissionWriter(Protocol):
    async def create_or_get(self, submission: NormalizedSubmission) -> FinalizedBuild: ...
```

Successful finalization must enqueue notice/review intents transactionally or through the existing durable event
store. A worker crash after the build commit and before publication must resume without creating another build or
losing either intent.

## Test matrix

- Preparation: every category, sponsor requested/unavailable/mismatched, attachment states, taxonomy duplicates,
  unknown values, missing/invalid form fields, and deterministic issue ordering.
- Codec: v1 fixtures, v2 strict round trip, unknown fields, enum values, canonical digest, corrupt payload, and mixed
  old/new worker rollout.
- Repository: enqueue/replay, claim expiry, lost token, attention/resubmit, retry/dead letter, completion, result
  migration upgrade/downgrade, and all state-shape constraints.
- Writer: source-draft idempotency, concurrent duplicate calls, exact category subtype, version/taxonomy resolution,
  sponsor attribution, and retry after commit ambiguity.
- Publication: crash after build commit, crash after first intent, replay of both intents, and no best-effort loss.
- HTTP transition: direct door request becomes a draft/finalization request with stable compatibility response or an
  explicitly versioned API break.
- Test quality: typed factories for routine setup; retained raw SQL is named and limited to schema/migration claims.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/submissions/application/finalization.py`: “bad design.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791476301) | **Fix in milestones 2–3.** Separate preparation, canonical writing, coordination, and durable publication. |
| [`squid/submissions/application/finalization.py`: “shouldn't these be their own handler, this is way too long of a function and the validation looks like they belong to some more sepcific classes”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791481026) | **Fix in milestone 2.** Group validators behind one typed preparation boundary rather than one class per condition. |
| [`squid/submissions/infrastructure/finalization_repository.py`: “there must be a better way. pydantic?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791496069) | **Fix in milestone 4.** Use a strict versioned Pydantic codec outside query orchestration. |
| [`squid/submissions/application/finalization.py`: “again wrong error class pattern”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791470286) | **Fix in milestone 3.** Expected repair issues become values; exceptions use the shared semantic hierarchy. |
| [`squid/submissions/application/finalization.py`: “not a good class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791470939) | **Fix in milestone 3.** Name the canonical `BuildSubmissionWriter`. |
| [`squid/submissions/application/finalization.py`: “not a good class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791471039) | **Fix in milestone 3.** Name notice and review publication actions explicitly. |
| [`squid/submissions/application/finalization.py`: “ban " without assuming a transport"”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791471769) | **Fix in milestone 3.** Docstrings state delivery/idempotency behavior, not abstract neutrality claims. |
| [`squid/submissions/infrastructure/finalization_models.py`: “tripl;e quoted string please”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791484535) | **Fix in milestone 6.** Name and triple-quote the state-shape SQL expressions. |
| [`squid/submissions/infrastructure/finalization_models.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791484840) | **Fix in milestone 6.** Map status through `FinalizationJobStatus`. |
| [`squid/submissions/infrastructure/finalization_models.py`: “should have a big cleanup into something like UUIDv7AuditBase”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791485512) | **Defer UUIDv7 to the existing schema-audit/TODO migration.** Do not create a divergent local base; deduplicate timestamps only if semantics and Alembic output match. |
| [`squid/submissions/infrastructure/finalization_models.py`: “1. what is target_key / 2. we are not storing a jsonb as provenance bro. Ban provenance, and wtf is this design.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791486676) | **Fix in milestone 5.** Reduce the result to typed build identity and drop both columns. |
| [`squid/submissions/infrastructure/finalization_repository.py`: “no need to use .value”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791487536) | **Fix in milestone 6.** Type mapped enum columns and compare enum members directly. |
| [`squid/builds/application/commands.py`: “Delete this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791448120) | **Fix in milestone 7 after migrating the live API caller.** Do not delete a still-used contract prematurely. |
| [`squid/builds/domain/models.py`: “delete this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791448855) | **Retain `BuildDraft` for current Discord/inference callers.** Its eventual removal is gated on their migration to revisioned drafts. |
| [`squid/submissions/application/finalization.py`: “these sort of utils exist in way too many locations”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791481744) | **Fix in milestones 2 and 4.** Pure answer extraction belongs to preparation; JSON conversion belongs to one codec. |
| [`squid/submissions/domain/finalization.py`: “doesnt these already exist, why are we duplicating again (a couple other classes here are duplicated too)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791483042) | **Fix in milestone 2.** Reuse canonical build/timing/dimension values where semantics match; retain distinct submission policy values only with explicit conversion tests. |
| [`squid/submissions/infrastructure/finalization_repository.py`: “again so many little utils scattered everywhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791496720) | **Fix in milestone 4.** Delete manual scalar/shape decoders after the codec lands. |
| [`tests/integration/builds/test_submission_targets.py`: “Don't use so many sql for this. maintenance hell.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791461730) | **Fix in milestone 8.** Use typed support factories for routine setup. |
| [`tests/integration/builds/test_submission_targets.py`: “this is too many SQL to validate... either helpers or sqlalchemy”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796604869) | **Fix in milestone 8.** Use mapped selects/helpers; retain SQL only for a named database-level claim. |
| [`tests/unit/builds/application/test_services.py`: “not worth testing. We should completely clean up these tests for removal of historical behavior”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791463041) | **Fix with milestone 7.** Delete delegation/history tests with the retired path, retaining behavior and invariant coverage. |

## Delivery and rollout

Preparation extraction and renames are behavior-preserving commits. The payload codec is dual-read before it is
write-v2. Drain or upgrade old workers before changing writers. Result-column removal follows reader inventory and
ships with downgrade coverage. The direct HTTP path is retired last, after generated clients and OpenAPI are updated.
