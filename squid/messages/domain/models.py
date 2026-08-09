"""Tracked message domain values."""

from dataclasses import dataclass
from typing import Literal

from whenever import Instant

MessagePurposeLiteral = Literal["view_pending_build", "view_confirmed_build", "vote", "build_original_message"]
ProjectionResourceKind = Literal["build", "vote_session"]


@dataclass(frozen=True, slots=True)
class TrackedMessage:
    """Discord message metadata needed for persistence."""

    id: int
    server_id: int
    channel_id: int
    author_id: int
    content: str | None


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """Stored message metadata exposed outside persistence."""

    id: int
    server_id: int
    channel_id: int | None
    author_id: int
    purpose: MessagePurposeLiteral
    content: str | None
    build_id: int | None
    vote_session_id: int | None
    updated_at: Instant | None
    projection_resource_kind: ProjectionResourceKind | None = None
    projection_source_key: str | None = None
    desired_action: Literal["refresh", "delete"] = "refresh"
    desired_revision: int = 1
    applied_revision: int = 1
