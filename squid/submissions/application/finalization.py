"""Durable submission finalization, driven the same way by the bot and the API."""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid5

from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError, JSONValue, ValidationError
from squid.core.i18n import tr
from squid.sponsors import PublicSponsor
from squid.submissions.application.drafts import DEFAULT_DRAFT_RETENTION_DAYS, StoredDraft, SubmissionDraftService
from squid.submissions.domain import DraftStatus, SubmissionOrigin
from squid.submissions.domain.finalization import (
    DoorOrientation,
    DoorSubmissionDetails,
    DoorTiming,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    ExtenderTiming,
    FinalizationJobStatus,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicArtifactState,
    SchematicRightsPolicy,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionSchematicLicense,
    SubmissionSchematicVisibility,
    SubmissionTargetResult,
    SubmissionTaxonomy,
)
from squid.submissions.errors import DraftIncompleteError, DraftSchemaUnsupportedError

logger = logging.getLogger(__name__)

MAX_FINALIZATION_JOB_CLAIM = 32
DEFAULT_FINALIZATION_ATTEMPTS = 3
_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class ClaimedFinalizationJob:
    """One durable job fenced to a worker by an unguessable claim UUID."""

    job_id: UUID
    draft_id: UUID
    draft_revision: int
    payload: NormalizedSubmission
    attempts: int
    claimed_at: Instant
    claim_token: UUID

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.claim_token.int == 0 or self.attempts < 1:
            msg = tr(t"claimed submission finalization metadata is invalid")
            raise DataIntegrityError(msg)


@dataclass(frozen=True, slots=True)
class FinalizationJobSnapshot:
    """Current durable state and retained outcome for a source draft."""

    job_id: UUID
    draft_id: UUID
    draft_revision: int
    status: FinalizationJobStatus
    attempts: int
    available_at: Instant
    claimed_at: Instant | None = None
    claim_token: UUID | None = None
    completed_at: Instant | None = None
    attention_at: Instant | None = None
    dead_at: Instant | None = None
    last_error: str | None = None
    issues: tuple[SubmissionAttentionIssue, ...] = ()
    result: SubmissionTargetResult | None = None


@dataclass(frozen=True, slots=True)
class FinalizationFailureOutcome:
    """Result of an unexpected failure transition guarded by a claim token."""

    applied: bool
    dead: bool


@dataclass(frozen=True, slots=True)
class SubmissionNotificationEvent:
    """Idempotent account notification emitted after a durable transition."""

    event_id: UUID
    draft_id: UUID
    owner_account_id: int
    status: FinalizationJobStatus
    build_id: int | None = None
    issues: tuple[SubmissionAttentionIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmissionReviewEvent:
    """Idempotent request to place a newly created build into staff review."""

    event_id: UUID
    draft_id: UUID
    build_id: int
    owner_account_id: int
    category: SubmissionCategory
    target_key: str


class ActionableSubmissionError(ValidationError):
    """A target rejected fields that the draft owner can repair."""

    def __init__(self, issues: Sequence[SubmissionAttentionIssue]) -> None:
        if not issues:
            msg = tr(t"actionable submission errors require at least one issue")
            raise InvalidStateError(msg)
        self.issues = tuple(issues)
        super().__init__(tr(t"Submission target rejected actionable fields."))


class DraftArtifactReadiness(Protocol):
    """Read backend-owned artifact state for one draft.

    Implementations must inspect every associated upload. They may expose media UUIDs
    only after normalization and a schematic UUID only after sanitization; pending or
    rejected uploads must be represented by stable attention issues.
    """

    async def assess(self, draft_id: UUID) -> SubmissionArtifactReadiness: ...


class SubmissionSponsorResolver(Protocol):
    """Resolve only an installation's currently authorized public sponsor projection."""

    async def resolve(self, installation_id: UUID) -> PublicSponsor | None: ...


class SubmissionTarget(Protocol):
    """Create or find a build using the source draft UUID as its idempotency key.

    Implementations must return the previously-created result when called again for the
    same ``source_draft_id``, including after the first call committed and the worker crashed.
    """

    async def create_or_get(self, submission: NormalizedSubmission) -> SubmissionTargetResult: ...


class SubmissionNotificationPort(Protocol):
    """Deliver an idempotent status notification without assuming a transport."""

    async def publish(self, event: SubmissionNotificationEvent) -> None: ...


class SubmissionReviewEventPort(Protocol):
    """Deliver an idempotent staff-review event without assuming a transport."""

    async def publish(self, event: SubmissionReviewEvent) -> None: ...


class FinalizationJobRepository(Protocol):
    """Atomic draft transitions and durable claim-token-fenced queue operations."""

    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot: ...

    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot: ...

    async def claim(self, *, now: Instant, limit: int) -> Sequence[ClaimedFinalizationJob]: ...

    async def complete(
        self,
        job: ClaimedFinalizationJob,
        result: SubmissionTargetResult,
        *,
        now: Instant,
    ) -> bool: ...

    async def needs_attention(
        self,
        job: ClaimedFinalizationJob,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> bool: ...

    async def fail(
        self,
        job: ClaimedFinalizationJob,
        error: str,
        *,
        now: Instant,
        retry_at: Instant,
        expires_at: Instant,
        max_attempts: int,
    ) -> FinalizationFailureOutcome: ...


class SubmissionFinalizationService:
    """Validate, assess, normalize, and atomically enqueue a draft."""

    def __init__(
        self,
        drafts: SubmissionDraftService,
        artifacts: DraftArtifactReadiness,
        jobs: FinalizationJobRepository,
        sponsors: SubmissionSponsorResolver | None = None,
        *,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"finalization attention retention must be positive")
            raise InvalidStateError(msg)
        self._drafts = drafts
        self._artifacts = artifacts
        self._jobs = jobs
        self._sponsors = sponsors
        self._retention_days = retention_days

    async def submit(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> FinalizationJobSnapshot:
        """Start idempotent processing or persist actionable preparation issues."""
        current = await self._drafts.get_owned(draft_id, account_id)
        if current.snapshot.status is DraftStatus.PROCESSING:
            existing = await self._jobs.get(draft_id)
            if existing is not None and existing.status in {
                FinalizationJobStatus.PENDING,
                FinalizationJobStatus.CLAIMED,
            }:
                return existing
            msg = tr(t"processing draft has no active finalization job")
            raise InvalidStateError(msg)
        if current.snapshot.status is DraftStatus.SUBMITTED:
            existing = await self._jobs.get(draft_id)
            if existing is None or existing.status is not FinalizationJobStatus.COMPLETED:
                msg = tr(t"submitted draft has no retained finalization result")
                raise InvalidStateError(msg)
            return existing

        touched_at = now or Instant.now()
        expires_at = touched_at.add(days=self._retention_days, days_assumed_24h_ok=True)
        try:
            validated = await self._drafts.validate_for_finalization(draft_id, account_id, locale=locale)
        except DraftIncompleteError as error:
            issues = _manifest_issues(error.public_context)
            return await self._jobs.record_preparation_attention(
                current,
                issues,
                now=touched_at,
                expires_at=expires_at,
            )
        except DraftSchemaUnsupportedError:
            return await self._jobs.record_preparation_attention(
                current,
                (SubmissionAttentionIssue("submission", SubmissionAttentionReason.SCHEMA_UNSUPPORTED),),
                now=touched_at,
                expires_at=expires_at,
            )

        sponsor, sponsor_issues = await self._resolve_sponsor(validated.draft, validated.normalized_answers)
        assessment = await self._artifacts.assess(draft_id)
        issues = _artifact_issues(validated.draft.origin, assessment)
        issues += _taxonomy_issues(validated.normalized_answers, validated.draft.snapshot.category)
        issues += sponsor_issues
        if issues:
            return await self._jobs.record_preparation_attention(
                validated.draft,
                issues,
                now=touched_at,
                expires_at=expires_at,
            )
        payload = _normalize(validated.draft, validated.normalized_answers, assessment, sponsor)
        return await self._jobs.enqueue(
            validated.draft,
            payload,
            now=touched_at,
            expires_at=expires_at,
        )

    async def status(self, draft_id: UUID, account_id: int) -> FinalizationJobSnapshot | None:
        """Return retained finalization state after rechecking draft ownership."""
        await self._drafts.get_owned(draft_id, account_id)
        return await self._jobs.get(draft_id)

    async def _resolve_sponsor(
        self,
        draft: StoredDraft,
        answers: Mapping[str, JSONValue],
    ) -> tuple[PublicSponsor | None, tuple[SubmissionAttentionIssue, ...]]:
        requested = draft.origin is SubmissionOrigin.PAPER and _required_bool(answers, "sponsor_attribution")
        if not requested:
            return None, ()
        if draft.source_installation_id is None or self._sponsors is None:
            return None, (_sponsor_unavailable(),)
        sponsor = await self._sponsors.resolve(draft.source_installation_id)
        if sponsor is None or sponsor.installation_id != draft.source_installation_id:
            return None, (_sponsor_unavailable(),)
        return sponsor, ()


class SubmissionFinalizationWorker:
    """Run bounded finalization batches with retry and dead-letter handling."""

    def __init__(
        self,
        jobs: FinalizationJobRepository,
        target: SubmissionTarget,
        notifications: SubmissionNotificationPort,
        reviews: SubmissionReviewEventPort,
        *,
        max_attempts: int = DEFAULT_FINALIZATION_ATTEMPTS,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
    ) -> None:
        if max_attempts < 1 or retention_days < 1:
            msg = tr(t"finalization retry and retention limits must be positive")
            raise InvalidStateError(msg)
        self._jobs = jobs
        self._target = target
        self._notifications = notifications
        self._reviews = reviews
        self._max_attempts = max_attempts
        self._retention_days = retention_days

    async def process_batch(self, *, limit: int = 8, now: Instant | None = None) -> None:
        """Claim and process at most ``limit`` jobs sequentially."""
        if not 1 <= limit <= MAX_FINALIZATION_JOB_CLAIM:
            maximum = MAX_FINALIZATION_JOB_CLAIM
            raise InvalidStateError(tr(t"finalization claim limit must be between 1 and {maximum}"))
        claimed_at = now or Instant.now()
        for job in await self._jobs.claim(now=claimed_at, limit=limit):
            await self._process(job, now=claimed_at)

    async def _process(self, job: ClaimedFinalizationJob, *, now: Instant) -> None:
        expires_at = now.add(days=self._retention_days, days_assumed_24h_ok=True)
        try:
            result = await self._target.create_or_get(job.payload)
        except ActionableSubmissionError as error:
            applied = await self._jobs.needs_attention(
                job,
                error.issues,
                now=now,
                expires_at=expires_at,
            )
            if applied:
                await self._notify_attention(job, error.issues)
            return
        except Exception as error:
            retry_at = now.add(seconds=_retry_delay(job.attempts))
            outcome = await self._jobs.fail(
                job,
                type(error).__name__,
                now=now,
                retry_at=retry_at,
                expires_at=expires_at,
                max_attempts=self._max_attempts,
            )
            if outcome.applied and outcome.dead:
                issues = (SubmissionAttentionIssue("submission", SubmissionAttentionReason.RETRY_EXHAUSTED),)
                await self._notify_attention(job, issues)
            return

        if not await self._jobs.complete(job, result, now=now):
            return
        await self._publish_success(job, result)

    async def _publish_success(self, job: ClaimedFinalizationJob, result: SubmissionTargetResult) -> None:
        notification = SubmissionNotificationEvent(
            event_id=uuid5(job.job_id, "submission-completed"),
            draft_id=job.draft_id,
            owner_account_id=job.payload.owner_account_id,
            status=FinalizationJobStatus.COMPLETED,
            build_id=result.build_id,
        )
        review = SubmissionReviewEvent(
            event_id=uuid5(job.job_id, "review-requested"),
            draft_id=job.draft_id,
            build_id=result.build_id,
            owner_account_id=job.payload.owner_account_id,
            category=job.payload.category,
            target_key=result.target_key,
        )
        await self._publish_safely(self._notifications, notification)
        await self._publish_safely(self._reviews, review)

    async def _notify_attention(
        self,
        job: ClaimedFinalizationJob,
        issues: tuple[SubmissionAttentionIssue, ...],
    ) -> None:
        event = SubmissionNotificationEvent(
            event_id=uuid5(job.job_id, f"needs-attention-{job.draft_revision}-{job.attempts}"),
            draft_id=job.draft_id,
            owner_account_id=job.payload.owner_account_id,
            status=FinalizationJobStatus.NEEDS_ATTENTION,
            issues=issues,
        )
        await self._publish_safely(self._notifications, event)

    @staticmethod
    async def _publish_safely(
        destination: SubmissionNotificationPort | SubmissionReviewEventPort,
        event: SubmissionNotificationEvent | SubmissionReviewEvent,
    ) -> None:
        try:
            if isinstance(event, SubmissionNotificationEvent):
                await cast(SubmissionNotificationPort, destination).publish(event)
            else:
                await cast(SubmissionReviewEventPort, destination).publish(event)
        except Exception:
            logger.exception(
                "Submission finalization event delivery failed",
                extra={"squid.submission.event_id": str(event.event_id)},
            )


def _manifest_issues(context: Mapping[str, JSONValue]) -> tuple[SubmissionAttentionIssue, ...]:
    field_errors = context.get("field_errors")
    if not isinstance(field_errors, Mapping):
        return (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)
    issues: list[SubmissionAttentionIssue] = []
    for field_id, reason in sorted(field_errors.items()):
        if not isinstance(reason, str):
            continue
        try:
            issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason(reason)))
        except ValueError:
            issues.append(SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED))
    return tuple(issues) or (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)


def _artifact_issues(
    origin: SubmissionOrigin,
    readiness: SubmissionArtifactReadiness,
) -> tuple[SubmissionAttentionIssue, ...]:
    issues = list(readiness.issues)
    match readiness.schematic_state:
        case SchematicArtifactState.ABSENT:
            if origin in {SubmissionOrigin.PAPER, SubmissionOrigin.FABRIC}:
                issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_REQUIRED))
        case SchematicArtifactState.PROCESSING:
            issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING))
        case SchematicArtifactState.REJECTED:
            issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_REJECTED))
        case SchematicArtifactState.SANITIZED:
            pass
    return _unique_issues(issues)


def _taxonomy_issues(
    answers: Mapping[str, JSONValue],
    category: str,
) -> tuple[SubmissionAttentionIssue, ...]:
    fields = ["restrictions", "showcase_tags"]
    if category in {SubmissionCategory.DOOR.value, SubmissionCategory.EXTENDER.value}:
        fields.append("patterns")
    issues: list[SubmissionAttentionIssue] = []
    for field_id in fields:
        value = answers.get(field_id, ())
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            values = [item for item in value if isinstance(item, str)]
            if len(values) != len(set(values)) or any(_STABLE_KEY.fullmatch(item) is None for item in values):
                issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason.UNKNOWN_OPTION))
    return tuple(issues)


def _normalize(
    draft: StoredDraft,
    answers: Mapping[str, JSONValue],
    readiness: SubmissionArtifactReadiness,
    sponsor: PublicSponsor | None,
) -> NormalizedSubmission:
    category = SubmissionCategory(draft.snapshot.category)
    visibility = SubmissionSchematicVisibility(_required_str(answers, "schematic_visibility"))
    public = visibility is SubmissionSchematicVisibility.PUBLIC_DOWNLOAD
    license_value = _optional_str(answers, "schematic_license") if public else None
    policy = SchematicRightsPolicy(
        visibility=visibility,
        license=SubmissionSchematicLicense(license_value) if license_value is not None else None,
        rights_attested=_required_bool(answers, "rights_attestation") if public else False,
        include_inventories=_required_bool(answers, "include_inventories"),
        include_free_text=_required_bool(answers, "include_free_text"),
    )
    details: DoorSubmissionDetails | ExtenderSubmissionDetails | GeneralSubmissionDetails
    if category is SubmissionCategory.DOOR:
        details = DoorSubmissionDetails(
            opening=SubmissionDimensions(
                _required_int(answers, "opening_width"),
                _required_int(answers, "opening_height"),
                _required_int(answers, "opening_depth"),
            ),
            orientation=DoorOrientation(_required_str(answers, "door_orientation")),
            pattern_keys=_string_tuple(answers, "patterns"),
            pattern_proposals=_string_tuple(answers, "pattern_proposals"),
            timing=DoorTiming(
                _optional_int(answers, "opening_time"),
                _optional_int(answers, "visible_opening_time"),
                _optional_int(answers, "closing_time"),
                _optional_int(answers, "visible_closing_time"),
            ),
        )
    elif category is SubmissionCategory.EXTENDER:
        details = ExtenderSubmissionDetails(
            orientation=ExtenderOrientation(_required_str(answers, "movement_orientation")),
            extension_length=_required_int(answers, "extension_length"),
            pattern_keys=_string_tuple(answers, "patterns"),
            pattern_proposals=_string_tuple(answers, "pattern_proposals"),
            timing=ExtenderTiming(
                _optional_int(answers, "extension_time"),
                _optional_int(answers, "retraction_time"),
            ),
        )
    else:
        details = GeneralSubmissionDetails()
    return NormalizedSubmission(
        source_draft_id=draft.snapshot.id,
        owner_account_id=draft.snapshot.owner_account_id,
        origin=draft.origin,
        schema_id=draft.snapshot.schema_id,
        schema_revision=draft.snapshot.schema_revision,
        category=category,
        display_name=_optional_nonblank_str(answers, "display_name"),
        description=_optional_str(answers, "description"),
        creators=_string_tuple(answers, "creators"),
        capture_dimensions=SubmissionDimensions(
            _required_int(answers, "capture_width"),
            _required_int(answers, "capture_height"),
            _required_int(answers, "capture_depth"),
        ),
        source_version=_required_str(answers, "source_version"),
        version_compatibility=_optional_str(answers, "version_compatibility"),
        taxonomy=SubmissionTaxonomy(
            restriction_keys=_string_tuple(answers, "restrictions"),
            restriction_proposals=_string_tuple(answers, "restriction_proposals"),
            showcase_tag_keys=_string_tuple(answers, "showcase_tags"),
        ),
        schematic_policy=policy,
        completion=_optional_str(answers, "completion"),
        ai_generated=_required_bool(answers, "ai_generated"),
        sponsor_attribution=(
            _required_bool(answers, "sponsor_attribution") if draft.origin is SubmissionOrigin.PAPER else False
        ),
        artifacts=readiness.artifacts,
        details=details,
        source_installation_id=draft.source_installation_id,
        sponsor=sponsor,
    )


def _sponsor_unavailable() -> SubmissionAttentionIssue:
    return SubmissionAttentionIssue("sponsor_attribution", SubmissionAttentionReason.SPONSOR_UNAVAILABLE)


def _required_str(answers: Mapping[str, JSONValue], field_id: str) -> str:
    value = answers.get(field_id)
    if not isinstance(value, str):
        raise InvalidStateError(tr(t"validated field {field_id} is not a string"))
    return value


def _optional_str(answers: Mapping[str, JSONValue], field_id: str) -> str | None:
    value = answers.get(field_id)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidStateError(tr(t"validated field {field_id} is not a string"))
    return value


def _optional_nonblank_str(answers: Mapping[str, JSONValue], field_id: str) -> str | None:
    value = _optional_str(answers, field_id)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _required_int(answers: Mapping[str, JSONValue], field_id: str) -> int:
    value = answers.get(field_id)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidStateError(tr(t"validated field {field_id} is not an integer"))
    return value


def _optional_int(answers: Mapping[str, JSONValue], field_id: str) -> int | None:
    value = answers.get(field_id)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidStateError(tr(t"validated field {field_id} is not an integer"))
    return value


def _required_bool(answers: Mapping[str, JSONValue], field_id: str) -> bool:
    value = answers.get(field_id)
    if not isinstance(value, bool):
        raise InvalidStateError(tr(t"validated field {field_id} is not a boolean"))
    return value


def _string_tuple(answers: Mapping[str, JSONValue], field_id: str) -> tuple[str, ...]:
    value = answers.get(field_id, ())
    if (
        not isinstance(value, Sequence)
        or isinstance(value, str | bytes)
        or not all(isinstance(item, str) for item in value)
    ):
        raise InvalidStateError(tr(t"validated field {field_id} is not a string list"))
    return tuple(cast(Sequence[str], value))


def _unique_issues(issues: Sequence[SubmissionAttentionIssue]) -> tuple[SubmissionAttentionIssue, ...]:
    return tuple(dict.fromkeys(issues))


def _retry_delay(attempts: int) -> int:
    return min(300, 2 ** min(attempts, 8))
