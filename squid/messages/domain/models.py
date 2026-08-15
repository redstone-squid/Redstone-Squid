"""Tracked message domain values."""

from dataclasses import dataclass
from typing import Literal

from whenever import Instant

MessagePurposeLiteral = Literal["view_pending_build", "view_confirmed_build", "vote", "build_original_message"]
"""Legacy tracking roles, retired as each writer moves onto `discord_posts`."""

ProjectionResourceKind = Literal["build", "vote_session"]


@dataclass(frozen=True, slots=True)
class MessageFact:
    """What is true about a Discord message, independent of why we care.

    One row per Discord message, shared by every use: a build's provenance, a
    starboard origin, and a vote target are all the same message, recorded once.
    """

    id: int
    channel_id: int
    author_id: int
    guild_id: int | None = None
    """None in DMs."""
    content: str | None = None
    created_at: Instant | None = None


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
    server_id: int | None
    channel_id: int | None
    author_id: int
    purpose: MessagePurposeLiteral | None
    content: str | None
    build_id: int | None
    vote_session_id: int | None
    updated_at: Instant | None
    created_at: Instant | None = None
    observed_at: Instant | None = None
    edited_at: Instant | None = None
    deleted_at: Instant | None = None
    projection_resource_kind: ProjectionResourceKind | None = None
    projection_source_key: str | None = None
    desired_action: Literal["refresh", "delete"] = "refresh"
    desired_revision: int = 1
    applied_revision: int = 1
