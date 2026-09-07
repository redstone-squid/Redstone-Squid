"""Atomic build, artifact-reference, receipt, and database-event submission writes."""

from dataclasses import replace
from typing import Protocol

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.domain import Build, SourceMessage
from squid.builds.infrastructure.repository import BuildRepository
from squid.core.errors import InvalidStateError
from squid.submissions.application.drafts import SubmissionDraftService
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
from squid.submissions.infrastructure.finalization_models import SubmissionReceiptMedia
from squid.submissions.infrastructure.finalization_repository import (
    _claimed_job,
    _locked_draft,
    _require_current_media,
    complete_in_session,
)


class SubmissionBuildPreparer(Protocol):
    """Resolve application-owned build facts before acquiring persistence locks."""

    async def prepare(self, submission: NormalizedSubmission) -> Build | BuildSubmissionRejected: ...


class PostgresSubmissionExecutor:
    """Commit a prepared source build and its receipt under the active claim fence."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        builds: BuildRepository,
        preparation: SubmissionBuildPreparer,
        drafts: SubmissionDraftService | None = None,
    ) -> None:
        self._sessions = sessions
        self._builds = builds
        self._preparation = preparation
        self._drafts = drafts

    async def execute(
        self,
        job: ClaimedFinalizationJob,
        *,
        now: Instant,
    ) -> FinalizedBuild | BuildSubmissionRejected | None:
        """Return no result for lost authority; a success includes the committed receipt."""
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
        async with self._sessions.begin() as session:
            draft = await _locked_draft(session, job.draft_id)
            if await _claimed_job(session, job) is None:
                return None
            if not await self._authorized(job):
                return BuildSubmissionRejected(
                    (SubmissionAttentionIssue("submission", SubmissionAttentionReason.PERMISSION_REVOKED),)
                )
            if (
                draft.status is not DraftStatus.PROCESSING
                or draft.owner_account_id != job.payload.owner_account_id
                or draft.revision != job.draft_revision
            ):
                message = "Submission claim no longer matches its source draft."
                raise InvalidStateError(message)
            candidate = replace(
                candidate, source_messages=TypeAdapter(tuple[SourceMessage, ...]).validate_python(draft.source_messages)
            )
            await _require_current_media(session, job.draft_id, job.payload.artifacts.normalized_media_upload_ids)
            existing = await self._builds.source_in_session(session, job.draft_id)
            if existing is not None:
                if (
                    existing.submitter_account_id != job.payload.owner_account_id
                    or existing.sponsor != candidate.sponsor
                ):
                    message = "Existing source build has conflicting immutable provenance."
                    raise InvalidStateError(message)
                candidate = existing
            else:
                await self._builds.insert_in_session(session, candidate)
            assert candidate.id is not None
            result = FinalizedBuild(candidate.id)
            if not await complete_in_session(session, job, result, now=now):
                message = "Submission authority changed inside its locked transaction."
                raise InvalidStateError(message)
            await session.flush()
            session.add_all(
                SubmissionReceiptMedia(job_id=job.job_id, upload_id=upload_id, created_at=now)
                for upload_id in job.payload.artifacts.normalized_media_upload_ids
            )
        return result

    async def _authorized(self, job: ClaimedFinalizationJob) -> bool:
        actor = job.requested_by_account_id or job.payload.owner_account_id
        if self._drafts is None:
            return actor == job.payload.owner_account_id
        try:
            await self._drafts.get_accessible(job.draft_id, actor)
        except DraftAccessDeniedError:
            return False
        return True
