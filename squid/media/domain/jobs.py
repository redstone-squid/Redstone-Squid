"""Persisted media job states and artifact roles."""

from enum import StrEnum


class MediaJobStatus(StrEnum):
    """Stable durable states for one processing request."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    DEAD = "dead"
    DISCARDED = "discarded"


class MediaArtifactRole(StrEnum):
    """The purpose of a durable artifact produced by processing."""

    OUTPUT = "output"
    VIDEO_THUMBNAIL = "video_thumbnail"
    REPORT = "report"
