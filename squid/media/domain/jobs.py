"""Persisted media job states and artifact roles."""

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
    # Legacy thumbnail role; the worker still writes it while older readers remain supported.
    POSTER = "poster"
    VIDEO_THUMBNAIL = "video_thumbnail"
    REPORT = "report"
