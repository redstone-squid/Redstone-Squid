"""Atomic build, artifact-reference, receipt, and database-event submission writes."""

from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.builds.application.ports import BuildUnitOfWork
from squid.builds.history import BuildChange
from squid.builds.models import Build
from squid.core.errors import InvalidStateError
from squid.submissions.application.drafts import DraftUnitOfWork, StoredDraft, SubmissionDraftService
from squid.submissions.application.finalization import ClaimedFinalizationJob
from squid.submissions.domain import (
    BuildSubmissionRejected,
    DraftStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.errors import DraftAccessDeniedError
from squid.unit_of_work import UnitOfWorkFactory


class SubmissionBuildPreparer(Protocol):
    """Resolve application-owned build facts before acquiring persistence locks."""

    async def prepare(self, submission: NormalizedSubmission) -> Build | BuildSubmissionRejected: ...


class BuildSubmissionExecutor:
    """Save a build and its finalization receipt in one transaction.

    The receipt links a finalization attempt to its created build. Saving both
    together lets a retry discover the result without creating another build.

    Prepare the candidate before locking the draft, keeping slow preparation
    outside the write transaction. Under the lock, recheck permission, claim
    ownership, draft revision, and attached media before saving.
    """

    def __init__(
        self,
        preparation: SubmissionBuildPreparer,
        drafts: SubmissionDraftService | None = None,
        *,
        uow_factory: UnitOfWorkFactory[SubmissionCommitUnitOfWork],
    ) -> None:
        self._uow_factory = uow_factory
        self._preparation = preparation
        self._drafts = drafts

    async def execute(
        self,
        job: ClaimedFinalizationJob,
        *,
        now: Instant,
    ) -> FinalizedBuild | BuildSubmissionRejected | None:
        """Return the committed build ID, a rejection, or None if the claim was lost.

        Revoked permission produces a rejection. An existing build with the same
        source draft is reused after checking its owner and sponsor details.
        """
        if not await self._authorized(job):
            return BuildSubmissionRejected(
                (SubmissionAttentionIssue("submission", SubmissionAttentionReason.PERMISSION_REVOKED),)
            )
        candidate = await self._preparation.prepare(job.payload)
        if isinstance(candidate, BuildSubmissionRejected):
            return candidate
        if (
            candidate.source_submission_draft_id != job.draft_id
            or candidate.submitter_account_id != job.payload.owner_account_id
            or candidate.sponsor != job.payload.sponsor
        ):
            message = "Prepared build does not match its submission provenance."
            raise InvalidStateError(message)
        async with self._uow_factory() as uow:
            draft = await uow.finalization_jobs.lock_claim(job)
            if draft is None:
                return None
            if not await self._authorized(job, uow):
                return BuildSubmissionRejected(
                    (SubmissionAttentionIssue("submission", SubmissionAttentionReason.PERMISSION_REVOKED),)
                )
            if (
                draft.snapshot.status is not DraftStatus.PROCESSING
                or draft.snapshot.owner_account_id != job.payload.owner_account_id
                or draft.snapshot.revision != job.draft_revision
            ):
                message = "Submission claim no longer matches its source draft."
                raise InvalidStateError(message)
            candidate = candidate.copy_for_edit(source_messages=draft.source_messages)
            await uow.finalization_jobs.require_current_media(
                job.draft_id, job.payload.artifacts.normalized_media_upload_ids
            )
            existing = await uow.builds.get_by_source_submission_draft_id(job.draft_id)
            if existing is not None:
                if (
                    existing.submitter_account_id != job.payload.owner_account_id
                    or existing.sponsor != candidate.sponsor
                ):
                    message = "Existing source build has conflicting immutable provenance."
                    raise InvalidStateError(message)
                candidate = existing
            else:
                await uow.builds.save(
                    candidate,
                    change=BuildChange(
                        operation="create",
                        source="submission_finalization",
                        actor_account_id=job.requested_by_account_id or job.payload.owner_account_id,
                        correlation_kind="submission_draft",
                        correlation_id=str(job.draft_id),
                    ),
                )
            assert candidate.id is not None
            result = FinalizedBuild(candidate.id)
            if not await uow.finalization_jobs.complete(job, result, now=now):
                message = "Submission authority changed inside its locked transaction."
                raise InvalidStateError(message)
            await uow.commit()
        return result

    async def _authorized(self, job: ClaimedFinalizationJob, uow: DraftUnitOfWork | None = None) -> bool:
        actor = job.requested_by_account_id or job.payload.owner_account_id
        if self._drafts is None:
            return actor == job.payload.owner_account_id
        try:
            if uow is None:
                await self._drafts.get_accessible(job.draft_id, actor)
            else:
                await self._drafts.get_accessible_in(uow, job.draft_id, actor)
        except DraftAccessDeniedError:
            return False
        return True


class SubmissionCommitRepository(Protocol):
    async def lock_claim(self, job: ClaimedFinalizationJob) -> StoredDraft | None: ...
    async def require_current_media(self, draft_id: UUID, upload_ids: tuple[UUID, ...]) -> None: ...
    async def complete(self, job: ClaimedFinalizationJob, result: FinalizedBuild, *, now: Instant) -> bool: ...


class SubmissionCommitUnitOfWork(BuildUnitOfWork, DraftUnitOfWork, Protocol):
    @property
    def finalization_jobs(self) -> SubmissionCommitRepository: ...
