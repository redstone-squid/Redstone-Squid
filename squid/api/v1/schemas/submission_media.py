"""Public transfer objects for account-owned draft media."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from squid.media.application.jobs import MediaArtifactRole, MediaJobSnapshot, MediaJobStatus
from squid.media.domain import MediaKind, MediaLimits


class StrictSchema(BaseModel):
    """Reject fields unknown to the published media contract."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DraftMediaStatus(StrEnum):
    """Stable client states that do not expose worker claim details.

    `processing` covers queued and in-flight normalization alike, so poll it. `completed`, `dead` and
    `discarded` are terminal: a dead upload failed normalization for good and never gains artifacts,
    and a discarded one was withdrawn by its owner.
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    DEAD = "dead"
    DISCARDED = "discarded"


class DraftMediaArtifactRole(StrEnum):
    """Normalized visual outputs visible to a draft owner.

    `output` is the re-encoded image or video; `poster` is a still frame, and only a video has one.
    """

    OUTPUT = "output"
    POSTER = "poster"


class DraftMediaArtifactResponse(StrictSchema):
    """Safe facts about one normalized visual artifact."""

    role: DraftMediaArtifactRole
    content_type: str = Field(description="Media type of the normalized file, not of the upload.")
    width: int = Field(description="Pixels.")
    height: int = Field(description="Pixels.")


class DraftMediaResponse(StrictSchema):
    """Safe state for one upload without storage or normalization internals."""

    id: UUID
    draft_id: UUID
    kind: MediaKind
    status: DraftMediaStatus
    source_content_type: str = Field(description="Media type of the bytes as uploaded.")
    artifacts: list[DraftMediaArtifactResponse] = Field(
        description="Empty until `status` is `completed`, and sorted by `role`."
    )

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
                if artifact.role in {MediaArtifactRole.OUTPUT, MediaArtifactRole.POSTER}
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
                    role=DraftMediaArtifactRole(artifact.role.value),
                    content_type=artifact.content_type,
                    width=_dimension(artifact.width),
                    height=_dimension(artifact.height),
                )
                for artifact in public_artifacts
            ],
        )


class DraftMediaLimitsResponse(StrictSchema):
    """Server-enforced upload, batch, and decoder-work budgets.

    Exceeding any of them is rejected server-side; a client that checks first spares the upload.
    """

    max_upload_bytes: int = Field(description="Total uploaded bytes across a draft, not one file.")
    max_images: int = Field(description="Images per draft.")
    max_videos: int = Field(description="Videos per draft.")
    max_output_bytes: int = Field(description="Total normalized bytes across a draft, not one file.")
    max_duration_milliseconds: int = Field(description="Longest accepted video, per file.")
    max_pixels_per_frame: int = Field(description="Width times height of one decoded frame, per file.")
    max_decoded_pixels_per_second: int = Field(
        description="Pixels per frame times frame rate, per video file. Images are exempt."
    )

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
    media: list[DraftMediaResponse] = Field(
        description="Every upload for the draft in creation order, including terminal ones."
    )


def _dimension(value: int | None) -> int:
    if value is None:
        msg = "Public visual media artifacts must have dimensions."
        raise ValueError(msg)
    return value
