"""Post destinations and resource references."""

from dataclasses import dataclass
from typing import Literal

ResourceKind = Literal["build", "vote_session", "starboard_entry", "inference_run", "submission_draft"]
"""Resource types supported by the post renderers."""

Surface = Literal["build_card", "build_review", "vote_card", "starboard_entry", "submission_status"]
"""A resource's presentation, such as a build review or a standalone vote card."""


def starboard_entry_key(starboard_id: int, origin_message_id: int) -> str:
    """Combine a board ID and source message ID into a starboard entry's resource key."""
    return f"{starboard_id}:{origin_message_id}"


@dataclass(frozen=True, slots=True)
class PostTarget:
    """The destination and presentation for a post to be sent."""

    channel_id: int
    guild_id: int
    surface: Surface


@dataclass(frozen=True, slots=True)
class PostReference:
    """The resource displayed by a message, used to route reactions and edits."""

    message_id: int
    resource_kind: ResourceKind
    resource_key: str
    surface: Surface
