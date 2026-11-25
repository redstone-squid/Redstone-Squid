"""Read the ready media outputs attached to approved builds."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.actors import ActingContext
from squid.core.artifacts import ArtifactStore
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.media.domain.jobs import MediaArtifactRole
from squid.core.media.domain.models import IMAGE_CONTENT_TYPES, VIDEO_CONTENT_TYPES
from squid.core.media.models import MediaArtifactObjectRecord, MediaArtifactRecord, MediaUploadRecord
from squid.core.submissions.models.finalization import SubmissionFinalizationResult, SubmissionReceiptMedia


class PublishedBuildMedia:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], artifacts: ArtifactStore, *, public_base_url: str
    ) -> None:
        self._sessions = sessions
        self.artifacts = artifacts
        self._public_base_url = public_base_url.rstrip("/")

    async def outputs(
        self, build_id: int, *, context: ActingContext, upload_id: UUID | None = None
    ) -> list[MediaArtifactRecord]:
        """Return outputs retained by a receipt for this build's own submission."""
        with context.scope():
            stmt = (
                select(MediaArtifactRecord)
                .join(MediaUploadRecord, MediaUploadRecord.id == MediaArtifactRecord.upload_id)
                .join(SubmissionReceiptMedia, SubmissionReceiptMedia.upload_id == MediaUploadRecord.id)
                .join(
                    SubmissionFinalizationResult, SubmissionFinalizationResult.job_id == SubmissionReceiptMedia.job_id
                )
                .join(Build, Build.id == SubmissionFinalizationResult.build_id)
                .join(MediaArtifactObjectRecord, MediaArtifactObjectRecord.object_key == MediaArtifactRecord.object_key)
                .where(
                    Build.id == build_id,
                    Build.submission_status == BuildStatus.APPROVED,
                    Build.source_submission_draft_id == MediaUploadRecord.draft_id,
                    MediaArtifactRecord.role == MediaArtifactRole.OUTPUT,
                    MediaArtifactRecord.content_type.in_(IMAGE_CONTENT_TYPES | VIDEO_CONTENT_TYPES),
                    MediaArtifactObjectRecord.deleted_at.is_(None),
                )
                .order_by(MediaArtifactRecord.id)
                .distinct()
            )
            if upload_id is not None:
                stmt = stmt.where(MediaUploadRecord.id == upload_id)
            async with self._sessions() as session:
                return list(await session.scalars(stmt))

    async def urls(self, build_id: int, *, context: ActingContext) -> tuple[list[str], list[str]]:
        """Return unique image and video URLs in retained output order."""
        with context.scope():
            images: list[str] = []
            videos: list[str] = []
            seen: set[str] = set()
            for artifact in await self.outputs(build_id, context=context):
                if artifact.sha256 in seen:
                    continue
                seen.add(artifact.sha256)
                url = f"{self._public_base_url}/v1/builds/{build_id}/media/{artifact.upload_id}/content"
                (images if artifact.content_type in IMAGE_CONTENT_TYPES else videos).append(url)
            return images, videos
