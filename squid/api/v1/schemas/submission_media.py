"""Public transfer objects for account-owned draft media."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.media.application.jobs import (
    MEDIA_VIDEO_THUMBNAIL_ROLES,
    MediaArtifactRole,
    MediaJobSnapshot,
    MediaJobStatus,
)
from squid.media.domain import MediaKind, MediaLimits


class StrictSchema(BaseModel):
    """Reject fields unknown to the published media contract."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DraftMediaStatus(StrEnum):
    """Stable client states that do not expose worker claim details."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    DEAD = "dead"
    DISCARDED = "discarded"


class DraftMediaArtifactRole(StrEnum):
    """Normalized visual outputs visible to a draft owner."""

    OUTPUT = "output"
    VIDEO_THUMBNAIL = "video_thumbnail"


class DraftMediaArtifactResponse(StrictSchema):
    """Safe facts about one normalized visual artifact."""

    role: DraftMediaArtifactRole
    content_type: str
    width: int
    height: int


class DraftMediaResponse(StrictSchema):
    """Safe state for one upload without storage or normalization internals."""

    id: UUID
    draft_id: UUID
    kind: MediaKind
    status: DraftMediaStatus
    source_content_type: str
    artifacts: list[DraftMediaArtifactResponse]

    @classmethod
    def from_snapshot(cls, snapshot: MediaJobSnapshot) -> Self:
        """Project a durable job snapshot onto the public owner-only contract."""
        status = {
            MediaJobStatus.PENDING: DraftMediaStatus.PROCESSING,
            MediaJobStatus.CLAIMED: DraftMediaStatus.PROCESSING,
            MediaJobStatus.COMPLETED: DraftMediaStatus.COMPLETED,
            MediaJobStatus.DEAD: DraftMediaStatus.DEAD,
            MediaJobStatus.DISCARDED: DraftMediaStatus.DISCARDED,
        }[snapshot.status]
        public_artifacts = sorted(
            (
                artifact
                for artifact in snapshot.artifacts
                if artifact.role is MediaArtifactRole.OUTPUT or artifact.role in MEDIA_VIDEO_THUMBNAIL_ROLES
            ),
            key=lambda artifact: artifact.role.value,
        )
        return cls(
            id=snapshot.upload.id,
            draft_id=snapshot.upload.draft_id,
            kind=snapshot.upload.kind,
            status=status,
            source_content_type=snapshot.upload.source_content_type,
            artifacts=[
                DraftMediaArtifactResponse(
                    role=(
                        DraftMediaArtifactRole.OUTPUT
                        if artifact.role is MediaArtifactRole.OUTPUT
                        else DraftMediaArtifactRole.VIDEO_THUMBNAIL
                    ),
                    content_type=artifact.content_type,
                    width=_dimension(artifact.width),
                    height=_dimension(artifact.height),
                )
                for artifact in public_artifacts
            ],
        )


class DraftMediaLimitsResponse(StrictSchema):
    """Server-enforced upload, batch, and decoder-work budgets."""

    max_upload_bytes: int
    max_images: int
    max_videos: int
    max_output_bytes: int
    max_duration_milliseconds: int
    max_pixels_per_frame: int
    max_decoded_pixels_per_second: int

    @classmethod
    def from_domain(cls, limits: MediaLimits) -> Self:
        return cls(
            max_upload_bytes=limits.max_source_bytes,
            max_images=limits.max_images,
            max_videos=limits.max_videos,
            max_output_bytes=limits.max_output_bytes,
            max_duration_milliseconds=limits.max_duration_milliseconds,
            max_pixels_per_frame=limits.max_pixels_per_frame,
            max_decoded_pixels_per_second=limits.max_decoded_pixels_per_second,
        )


class DraftMediaListResponse(StrictSchema):
    """The complete bounded media collection for one owned draft."""

    limits: DraftMediaLimitsResponse
    media: list[DraftMediaResponse]


def _dimension(value: int | None) -> int:
    if value is None:
        msg = "Public visual media artifacts must have dimensions."
        raise ValueError(msg)
    return value
