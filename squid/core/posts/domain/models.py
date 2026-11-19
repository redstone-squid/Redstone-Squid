"""Post destinations and resource references."""

from dataclasses import dataclass
from typing import Literal, override

from squid.core.errors import ValidationError

ResourceKind = Literal["build", "vote_session", "starboard_entry", "inference_run", "submission_draft"]
"""Resource types supported by the post renderers."""

Surface = Literal["build_card", "build_review", "vote_card", "starboard_entry", "submission_status"]
"""A resource's presentation, such as a build review or a standalone vote card."""


@dataclass(frozen=True, slots=True)
class StarboardEntryKey:
    """The board and origin message identifying one mirrored entry."""

    starboard_id: int
    origin_message_id: int

    def __post_init__(self) -> None:
        if self.starboard_id <= 0 or self.origin_message_id <= 0:
            message = "Starboard entry IDs must be positive"
            raise ValidationError(message)

    @classmethod
    def parse(cls, value: str) -> StarboardEntryKey:
        board, message = value.split(":")
        return cls(int(board), int(message))

    @override
    def __str__(self) -> str:
        return f"{self.starboard_id}:{self.origin_message_id}"


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
