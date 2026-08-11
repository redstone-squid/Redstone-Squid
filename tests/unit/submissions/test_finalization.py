"""Transport-neutral submission finalization tests."""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

import pytest
from whenever import Instant

from squid.core.errors import JSONValue
from squid.submissions.application import (
    ActionableSubmissionError,
    AppliedDraftChange,
    ClaimedFinalizationJob,
    FinalizationFailureOutcome,
    FinalizationJobSnapshot,
    StoredDraft,
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFinalizationWorker,
    SubmissionNotificationEvent,
    SubmissionReviewEvent,
    build_submission_manifest,
)
from squid.submissions.domain import (
    DoorSubmissionDetails,
    DraftChange,
    DraftSnapshot,
    DraftStatus,
    ExtenderSubmissionDetails,
    FinalizationJobStatus,
    FormManifest,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicArtifactState,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionOrigin,
    SubmissionTargetResult,
)
from squid.submissions.errors import DraftAccessDeniedError

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000401")
JOB_ID = UUID("00000000-0000-4000-8000-000000000402")
CLAIM_TOKEN = UUID("00000000-0000-4000-8000-000000000403")
SCHEMATIC_ID = UUID("00000000-0000-4000-8000-000000000404")
NOW = Instant.parse_iso("2026-08-11T16:00:00Z")


class FakeManifestRegistry:
    def __init__(self) -> None:
        self.manifest = build_submission_manifest("en")

    async def current(self, *, locale: str | None) -> FormManifest:
        del locale
        return self.manifest

    async def get(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        del locale
        if (schema_id, revision) == (self.manifest.schema_id, self.manifest.revision):
            return self.manifest
        return None


class FakeDraftRepository:
    def __init__(self, draft: StoredDraft) -> None:
        self.draft = draft

    async def count_active_for_account(self, account_id: int) -> int:
        del account_id
        return 1

    async def create(self, draft: StoredDraft) -> StoredDraft:
        self.draft = draft
        return draft

    async def get(self, draft_id: UUID) -> StoredDraft | None:
        return self.draft if draft_id == self.draft.snapshot.id else None

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: str,
    ) -> AppliedDraftChange | None:
        del draft_id, account_id, idempotency_key
        return None

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftChange:
        del draft_id, account_id, change, updated_at, expires_at
        raise NotImplementedError

    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        updated_at: Instant,
        expires_at: Instant,
    ) -> StoredDraft:
        del draft_id, account_id, expected_revision
        self.draft = replace(
            self.draft,
            snapshot=self.draft.snapshot.transition(status),
            updated_at=updated_at,
            expires_at=expires_at,
        )
        return self.draft

    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool:
        del draft_id, account_id
        return False

    async def expire_due(self, *, now: Instant, limit: int = 100) -> int:
        del now, limit
        return 0


class FakeArtifacts:
    def __init__(self, readiness: SubmissionArtifactReadiness) -> None:
        self.readiness = readiness
        self.assessed: list[UUID] = []

    async def assess(self, draft_id: UUID) -> SubmissionArtifactReadiness:
        self.assessed.append(draft_id)
        return self.readiness


class FakeFinalizationJobs:
    def __init__(self) -> None:
        self.snapshot: FinalizationJobSnapshot | None = None
        self.enqueued: NormalizedSubmission | None = None
        self.attention: tuple[SubmissionAttentionIssue, ...] = ()
        self.claimed: tuple[ClaimedFinalizationJob, ...] = ()
        self.completed: SubmissionTargetResult | None = None
        self.worker_attention: tuple[SubmissionAttentionIssue, ...] = ()
        self.failures: list[str] = []

    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None:
        assert draft_id == DRAFT_ID
        return self.snapshot

    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot:
        assert draft.snapshot.id == DRAFT_ID
        assert expires_at > now
        self.enqueued = payload
        self.snapshot = _snapshot(FinalizationJobStatus.PENDING)
        return self.snapshot

    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot:
        assert draft.snapshot.id == DRAFT_ID
        assert expires_at > now
        self.attention = tuple(issues)
        self.snapshot = replace(
            _snapshot(FinalizationJobStatus.NEEDS_ATTENTION),
            attention_at=now,
            issues=self.attention,
        )
        return self.snapshot

    async def claim(self, *, now: Instant, limit: int) -> tuple[ClaimedFinalizationJob, ...]:
        assert limit >= 1
        del now
        return self.claimed

    async def complete(
        self,
        job: ClaimedFinalizationJob,
        result: SubmissionTargetResult,
        *,
        now: Instant,
    ) -> bool:
        assert job in self.claimed
        del now
        self.completed = result
        return True

    async def needs_attention(
        self,
        job: ClaimedFinalizationJob,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> bool:
        assert job in self.claimed
        assert expires_at > now
        self.worker_attention = tuple(issues)
        return True

    async def fail(
        self,
        job: ClaimedFinalizationJob,
        error: str,
        *,
        now: Instant,
        retry_at: Instant,
        expires_at: Instant,
        max_attempts: int,
    ) -> FinalizationFailureOutcome:
        assert job in self.claimed
        assert retry_at > now
        assert expires_at > now
        self.failures.append(error)
        return FinalizationFailureOutcome(applied=True, dead=job.attempts >= max_attempts)


class FakeTarget:
    def __init__(self, result: SubmissionTargetResult | Exception) -> None:
        self.result = result
        self.payloads: list[NormalizedSubmission] = []

    async def create_or_get(self, submission: NormalizedSubmission) -> SubmissionTargetResult:
        self.payloads.append(submission)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeNotifications:
    def __init__(self) -> None:
        self.events: list[SubmissionNotificationEvent] = []

    async def publish(self, event: SubmissionNotificationEvent) -> None:
        self.events.append(event)


class FakeReviews:
    def __init__(self) -> None:
        self.events: list[SubmissionReviewEvent] = []

    async def publish(self, event: SubmissionReviewEvent) -> None:
        self.events.append(event)


def _answers() -> dict[str, JSONValue]:
    return {
        "display_name": "Compact vault",
        "description": "A useful mechanism",
        "capture_width": 3,
        "capture_height": 4,
        "capture_depth": 5,
        "source_version": "26.1.2",
        "version_compatibility": "26.1.x",
        "creators": ["Builder"],
        "restrictions": ["locational"],
        "restriction_proposals": ["Needs a new restriction"],
        "showcase_tags": ["compact"],
        "schematic_visibility": "reviewer_only",
    }


def _stored(
    origin: SubmissionOrigin,
    *,
    category: str = "other",
    answers: dict[str, JSONValue] | None = None,
) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="build_submission.v1",
            schema_revision=1,
            category=category,
            answers=answers or _answers(),
        ),
        origin=origin,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )


def _snapshot(status: FinalizationJobStatus) -> FinalizationJobSnapshot:
    return FinalizationJobSnapshot(
        job_id=JOB_ID,
        draft_id=DRAFT_ID,
        draft_revision=0,
        status=status,
        attempts=0,
        available_at=NOW,
    )


async def _submit(
    origin: SubmissionOrigin,
    readiness: SubmissionArtifactReadiness,
    *,
    category: str = "other",
    answers: dict[str, JSONValue] | None = None,
) -> tuple[FinalizationJobSnapshot, FakeFinalizationJobs]:
    repository = FakeDraftRepository(_stored(origin, category=category, answers=answers))
    drafts = SubmissionDraftService(repository, FakeManifestRegistry())
    jobs = FakeFinalizationJobs()
    service = SubmissionFinalizationService(drafts, FakeArtifacts(readiness), jobs)
    return await service.submit(DRAFT_ID, 7, locale="en", now=NOW), jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [SubmissionOrigin.PAPER, SubmissionOrigin.FABRIC])
async def test_minecraft_origins_require_backend_verified_sanitized_schematic(origin: SubmissionOrigin) -> None:
    result, jobs = await _submit(origin, SubmissionArtifactReadiness())

    assert result.status is FinalizationJobStatus.NEEDS_ATTENTION
    assert jobs.enqueued is None
    assert jobs.attention == (SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_REQUIRED),)


@pytest.mark.asyncio
async def test_web_can_omit_schematic_and_enqueues_typed_payload_without_type_label() -> None:
    result, jobs = await _submit(SubmissionOrigin.WEB, SubmissionArtifactReadiness())

    assert result.status is FinalizationJobStatus.PENDING
    assert jobs.enqueued is not None
    assert jobs.enqueued.source_draft_id == DRAFT_ID
    assert jobs.enqueued.owner_account_id == 7
    assert jobs.enqueued.display_name == "Compact vault"
    assert jobs.enqueued.category is SubmissionCategory.OTHER
    assert isinstance(jobs.enqueued.details, GeneralSubmissionDetails)
    assert jobs.enqueued.taxonomy.restriction_keys == ("locational",)
    assert not hasattr(jobs.enqueued, "type_label")


@pytest.mark.asyncio
async def test_any_supplied_schematic_must_finish_server_sanitization() -> None:
    result, jobs = await _submit(
        SubmissionOrigin.DISCORD,
        SubmissionArtifactReadiness(schematic_state=SchematicArtifactState.PROCESSING),
    )

    assert result.status is FinalizationJobStatus.NEEDS_ATTENTION
    assert jobs.attention == (SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING),)


@pytest.mark.asyncio
async def test_server_sanitized_artifact_identity_reaches_target_payload() -> None:
    result, jobs = await _submit(
        SubmissionOrigin.FABRIC,
        SubmissionArtifactReadiness(
            schematic_state=SchematicArtifactState.SANITIZED,
            sanitized_schematic_id=SCHEMATIC_ID,
        ),
    )

    assert result.status is FinalizationJobStatus.PENDING
    assert jobs.enqueued is not None
    assert jobs.enqueued.artifacts.sanitized_schematic_id == SCHEMATIC_ID


@pytest.mark.asyncio
async def test_public_license_rights_and_sanitizer_privacy_policy_are_retained() -> None:
    answers = _answers() | {
        "schematic_visibility": "public_download",
        "schematic_license": "cc_by_sa_4_0",
        "rights_attestation": True,
        "include_inventories": False,
        "include_free_text": False,
    }
    _, jobs = await _submit(
        SubmissionOrigin.WEB,
        SubmissionArtifactReadiness(
            schematic_state=SchematicArtifactState.SANITIZED,
            sanitized_schematic_id=SCHEMATIC_ID,
        ),
        answers=answers,
    )

    assert jobs.enqueued is not None
    assert jobs.enqueued.schematic_policy.license is not None
    assert jobs.enqueued.schematic_policy.license.value == "cc_by_sa_4_0"
    assert jobs.enqueued.schematic_policy.rights_attested is True
    assert jobs.enqueued.schematic_policy.include_inventories is False
    assert jobs.enqueued.schematic_policy.include_free_text is False


@pytest.mark.asyncio
async def test_door_dimensions_patterns_and_timings_are_typed() -> None:
    answers = _answers() | {
        "opening_width": 2,
        "opening_height": 3,
        "opening_depth": 1,
        "door_orientation": "skydoor",
        "patterns": ["seamless"],
        "pattern_proposals": ["Novel layout"],
        "opening_time": 8,
        "visible_opening_time": 4,
        "closing_time": 9,
        "visible_closing_time": 5,
    }
    _, jobs = await _submit(
        SubmissionOrigin.WEB,
        SubmissionArtifactReadiness(),
        category="door",
        answers=answers,
    )

    assert jobs.enqueued is not None
    assert isinstance(jobs.enqueued.details, DoorSubmissionDetails)
    assert jobs.enqueued.details.opening.width == 2
    assert jobs.enqueued.details.pattern_keys == ("seamless",)
    assert jobs.enqueued.details.timing.visible_closing == 5


@pytest.mark.asyncio
async def test_extender_movement_patterns_and_timings_are_typed() -> None:
    answers = _answers() | {
        "movement_orientation": "vertical_up",
        "extension_length": 12,
        "patterns": ["double_extender"],
        "extension_time": 6,
        "retraction_time": 7,
    }
    _, jobs = await _submit(
        SubmissionOrigin.WEB,
        SubmissionArtifactReadiness(),
        category="extender",
        answers=answers,
    )

    assert jobs.enqueued is not None
    assert isinstance(jobs.enqueued.details, ExtenderSubmissionDetails)
    assert jobs.enqueued.details.extension_length == 12
    assert jobs.enqueued.details.pattern_keys == ("double_extender",)
    assert jobs.enqueued.details.timing.retraction == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["utility", "entrance", "other"])
async def test_general_categories_share_an_explicit_empty_details_value(category: str) -> None:
    _, jobs = await _submit(
        SubmissionOrigin.WEB,
        SubmissionArtifactReadiness(),
        category=category,
    )

    assert jobs.enqueued is not None
    assert jobs.enqueued.category.value == category
    assert isinstance(jobs.enqueued.details, GeneralSubmissionDetails)


@pytest.mark.asyncio
async def test_manifest_failures_are_retained_as_stable_attention_codes() -> None:
    answers = _answers()
    del answers["source_version"]
    result, jobs = await _submit(SubmissionOrigin.WEB, SubmissionArtifactReadiness(), answers=answers)

    assert result.status is FinalizationJobStatus.NEEDS_ATTENTION
    assert SubmissionAttentionIssue("source_version", SubmissionAttentionReason.REQUIRED) in jobs.attention


@pytest.mark.asyncio
async def test_status_rechecks_draft_ownership_before_returning_job() -> None:
    repository = FakeDraftRepository(_stored(SubmissionOrigin.WEB))
    drafts = SubmissionDraftService(repository, FakeManifestRegistry())
    jobs = FakeFinalizationJobs()
    jobs.snapshot = _snapshot(FinalizationJobStatus.PENDING)
    service = SubmissionFinalizationService(drafts, FakeArtifacts(SubmissionArtifactReadiness()), jobs)

    assert await service.status(DRAFT_ID, 7) == jobs.snapshot

    with pytest.raises(DraftAccessDeniedError):
        await service.status(DRAFT_ID, 8)


async def _payload() -> NormalizedSubmission:
    repository = FakeDraftRepository(_stored(SubmissionOrigin.WEB))
    drafts = SubmissionDraftService(repository, FakeManifestRegistry())
    jobs = FakeFinalizationJobs()
    service = SubmissionFinalizationService(drafts, FakeArtifacts(SubmissionArtifactReadiness()), jobs)

    await service.submit(DRAFT_ID, 7, locale="en", now=NOW)
    assert jobs.enqueued is not None
    return jobs.enqueued


def _claim(payload: NormalizedSubmission, attempts: int = 1) -> ClaimedFinalizationJob:
    return ClaimedFinalizationJob(
        job_id=JOB_ID,
        draft_id=DRAFT_ID,
        draft_revision=0,
        payload=payload,
        attempts=attempts,
        claimed_at=NOW,
        claim_token=CLAIM_TOKEN,
    )


@pytest.mark.asyncio
async def test_worker_completes_and_emits_transport_neutral_events() -> None:
    payload = await _payload()
    jobs = FakeFinalizationJobs()
    jobs.claimed = (_claim(payload),)
    target_result = SubmissionTargetResult(41, "postgres_builds", {"source": "draft"})
    notifications = FakeNotifications()
    reviews = FakeReviews()
    worker = SubmissionFinalizationWorker(jobs, FakeTarget(target_result), notifications, reviews)

    await worker.process_batch(now=NOW)

    assert jobs.completed == target_result
    assert notifications.events[0].build_id == 41
    assert reviews.events[0].build_id == 41
    assert notifications.events[0].event_id != reviews.events[0].event_id


@pytest.mark.asyncio
async def test_actionable_target_failure_returns_draft_to_attention() -> None:
    payload = await _payload()
    issue = SubmissionAttentionIssue("restrictions", SubmissionAttentionReason.TARGET_REJECTED)
    jobs = FakeFinalizationJobs()
    jobs.claimed = (_claim(payload),)
    notifications = FakeNotifications()
    worker = SubmissionFinalizationWorker(
        jobs,
        FakeTarget(ActionableSubmissionError((issue,))),
        notifications,
        FakeReviews(),
    )

    await worker.process_batch(now=NOW)

    assert jobs.worker_attention == (issue,)
    assert notifications.events[0].issues == (issue,)


@pytest.mark.asyncio
async def test_unexpected_failure_retries_then_notifies_after_dead_letter() -> None:
    payload = await _payload()
    jobs = FakeFinalizationJobs()
    jobs.claimed = (_claim(payload, attempts=3),)
    notifications = FakeNotifications()
    worker = SubmissionFinalizationWorker(jobs, FakeTarget(RuntimeError("secret")), notifications, FakeReviews())

    await worker.process_batch(now=NOW)

    assert jobs.failures == ["RuntimeError"]
    assert notifications.events[0].issues == (
        SubmissionAttentionIssue("submission", SubmissionAttentionReason.RETRY_EXHAUSTED),
    )
