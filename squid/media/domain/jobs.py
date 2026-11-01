"""Persisted submission records."""

from enum import StrEnum


class MediaJobStatus(StrEnum):
    """Stable durable states for one normalization request."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    DEAD = "dead"
    DISCARDED = "discarded"


class MediaArtifactRole(StrEnum):
    """The purpose of a durable artifact produced by normalization."""

    OUTPUT = "output"
    # Read-only compatibility for rows written before the video-thumbnail rollout.
    POSTER = "poster"
    VIDEO_THUMBNAIL = "video_thumbnail"
    REPORT = "report"
