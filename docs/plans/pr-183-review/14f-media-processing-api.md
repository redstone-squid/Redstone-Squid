# PR #183 Review 14F: Media Processing and API

## Scope

This plan covers eleven threads across private streaming uploads, normalization values/jobs, persistence helpers,
optional worker registration, deployment tests, and the large media API contract test. “Media” remains an internal
package term for image/video processing; submitter-facing form/error vocabulary is addressed in 14D.

## Findings and decisions

### The route module owns application orchestration

`squid/api/v1/submission_media.py` currently declares service/runtime protocols, structured errors, dependency
resolution, upload staging, header validation, ownership checks, job submission, response cache policy, and four
routes. Streaming bytes from `Request` into a private bounded file is legitimately HTTP-adapter work. Deciding
whether a draft accepts an upload, registering it, cleaning an abandoned stage, and mapping job state are application
work.

Create `DraftAttachmentService` under `submissions.application.attachments`; submission ownership is the aggregate
boundary and media job registration is a narrow injected port. Before reading attacker-controlled bytes, the route
calls `authorize_upload` and receives a short-lived typed authority for that draft/account/revision. It then validates
framing and streams to a `StagedUpload`. `register` consumes the authority, rechecks ownership/state/revision under
the lifecycle lock, registers or replays the upload, owns cleanup on every exit, and returns typed snapshots.
Dependency aliases move to `squid/api/dependencies.py`; route-local runtime protocols and errors move to their owners.

### Limits should return all actionable violations

`MediaLimits.batch_violation` and `probe_violation` return only the first failure. A user can repeatedly upload the
same file and discover one problem at a time.

Replace them with deterministic `batch_violations`/`probe_violations` tuples and make `MediaLimitExceededError`
carry the complete public-safe list. Keep a compatibility `first_violation` helper only for an internal branch that
cannot present more than one, and remove it once callers migrate. Never reveal object keys, filenames, or probe
metadata beyond the allowed measures/limits.

### “Poster” is ambiguous outside video tooling

Rename the durable role/value to `VIDEO_THUMBNAIL` (`video_thumbnail` on new writes) and user copy to “video preview
image.” Expand database checks/readers first, deploy dual readers, switch writers, drain old writers, backfill rows,
then contract. Historical schema-1 report JSON is content-addressed immutable data: retain its `poster` bytes/object
key and introduce new terminology only in a new report schema if an inventoried reader requires it. Include
submission readiness and API DTO role mapping in the consumer inventory.

### Persistence enums and object publication need one boundary

Map job status, upload kind, and artifact role columns to their `StrEnum`s while retaining text/check constraints.
The repository repeats artifact-object registration/publication lease steps. Extract a private
`ArtifactPublicationRepository` or named helper that owns object-row upsert, lease acquisition/renewal/release, and
claim-token fencing; do not create a generic “repository utils” bag.

### Optional jobs should be registered, not checked repeatedly

`WorkerApplication` checks `media_runner` during startup, readiness, interval calculation, and execution. Build a
tuple of `WorkerJobSpec`s once from available services. Readiness and scheduling derive from those registered specs;
the media callback itself can require a non-optional runner captured during registration.

### Deployment tests inspect source rather than the image contract

The exact interpreter/tool/user/directory claims are runtime image properties. Replace Dockerfile/Compose string
matching with build-and-inspect tests for the relevant targets, reusing 14A's named stages. Keep cheap pure tests for
resource arithmetic and settings documentation; they do not need Docker.

### API and error text still need localization

Move route-local errors into `squid/media/errors.py`, author public messages/actions as deferred text, and let the
shared FastAPI error renderer localize them. OpenAPI descriptions remain stable English source strings and are
extracted through the normal catalogue workflow.

## Planned work

1. **Pin the live HTTP and job contracts.** Cover body framing, private modes, ownership-before-read, cleanup, replay,
   cache headers, safe DTO fields, and worker-disabled behavior.
2. **Extract `DraftAttachmentService`.** Add ownership-before-read authorization plus register-time revalidation;
   leave only HTTP parsing/streaming/response assembly in the route and move dependencies/errors to their owners.
3. **Aggregate limit violations.** Update domain, application errors, API schemas, Discord/web presentation, and
   tests with deterministic order.
4. **Rename poster to video thumbnail.** Expand readers/checks, switch and drain writers, backfill mutable rows, then
   contract. Preserve immutable schema-1 report bytes and object keys; add a new report schema only for a real reader.
5. **Type persistence and extract artifact publication.** Remove scattered `.value` conversions and centralize the
   lease/fencing transaction.
6. **Register worker job specs once.** Derive scheduling/readiness/health from the same immutable registry.
7. **Replace source-shape deployment tests.** Build/inspect named targets and keep non-Docker documentation arithmetic
   tests separate.
8. **Localize public failures and reorganize tests.** Split the 489-line route test into streaming transport,
   application service, DTO/OpenAPI, and disabled-feature modules with typed fakes.

## Interface sketch

```python
@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    byte_size: int
    sha256: str
    content_type: str


class DraftAttachmentService:
    async def authorize_upload(
        self, *, draft_id: UUID, account_id: int, kind: MediaKind
    ) -> DraftUploadAuthority: ...

    async def register(
        self,
        *,
        authority: DraftUploadAuthority,
        staged: StagedUpload,
        strip_audio: bool,
        upload_id: UUID | None,
    ) -> MediaJobSnapshot: ...


@dataclass(frozen=True, slots=True)
class WorkerJobSpec:
    name: str
    interval_seconds: float
    critical: bool
    run: Callable[[], Awaitable[None]]
```

`StagedUpload` hands out authority to one private temporary file; consuming or discarding it ends that authority.
The concrete type's first docstring paragraph must state that lifetime when implemented.

## Test matrix

- Transport: duplicate/missing content length, transfer encoding, content type, nil UUID, query allowlist, short/long
  body, disconnect, filesystem failure, permissions, cleanup, no-store, and safe response fields.
- Application: denial before the first body read, authority expiry/revision race, register-time ownership recheck,
  replay with same/different metadata, aggregate limits, staged-file authority, registration failure, and cleanup
  failure containment.
- Repository: enum round trips, concurrent registration, artifact publication lease, cleanup fencing, shared object
  references, claim loss, terminal source deletion, and poster-to-thumbnail migration.
- Worker: optional service absent/present, job registry/readiness parity, interval choice, heartbeats, bounded
  concurrency, retry/dead-letter, and shutdown.
- Deployment: image builds for base/media/GPU combinations; exact tool versions, non-root UID, writable modes, and no
  media tools in the base target.
- Localization: every public error title/detail/action in two locales, with OpenAPI source descriptions unchanged.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/api/v1/submission_media.py`: “defining everything here in one file is lazy, and wrong.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796630410) | **Fix in milestones 2 and 8.** Keep HTTP streaming in the adapter; move policy, dependencies, and errors to owners. |
| [`squid/api/v1/submission_media.py`: “we are not implementing a service in the api layer”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796635009) | **Fix in milestone 2.** Introduce the application-owned `DraftAttachmentService`. |
| [`tests/unit/submissions/test_media_api_contract.py`: “this whole commit is too lazy and should be refactored heavvily”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796638770) | **Fix in milestone 8.** Split tests by transport/application/schema/feature boundary and type their fakes. |
| [`squid/worker/app.py`: “ugly to have so many individual checks for self._services.media_runner”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796721953) | **Fix in milestone 6.** Register optional `WorkerJobSpec`s once and derive health/scheduling from them. |
| [`squid/media/application/jobs.py`: “actually im not a fan of this being named Poster”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791222701) | **Fix in milestone 4.** Rename the role to video thumbnail with rolling compatibility. |
| [`squid/media/errors.py`: “users won't understand "normalized" i think, and won't know what exactly are we trying to do and how we are failing.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790905436) | **Fix in milestones 3 and 8.** Present attachment processing and concrete next actions; keep normalization internal. |
| [`squid/media/infrastructure/models.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791226249) | **Fix in milestone 5.** Type checked text columns with domain enums. |
| [`squid/media/infrastructure/repository.py`: “I feel like this should be a helper”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803745065) | **Fix in milestone 5.** Extract one cohesive artifact-publication/lease boundary, not miscellaneous utilities. |
| [`squid/media/domain/models.py`: “why only return one violation?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790902570) | **Fix in milestone 3.** Return every public-safe violation in stable order. |
| [`tests/deployment/test_media_worker_image.py`: “don't test by reading strings...”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3799634252) | **Fix in milestone 7.** Build and inspect the image contract. |
| [`squid/api/v1/submission_media.py`: “translation”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796622125) | **Fix in milestone 8.** Route errors use shared deferred localization. |

## Delivery and rollout

Extract the service before renaming persisted values. The thumbnail migration is expand/deploy readers/switch
writers/drain/backfill/contract; the old reader remains until retained rows/jobs are gone. Immutable report objects
are never rewritten under stable content-addressed keys. Worker job registration and image-test changes are
independent commits. No milestone deletes user artifacts or object keys.
