"""Bot-owned Discord post domain values."""

from dataclasses import dataclass
from typing import Literal

from whenever import Instant

ResourceKind = Literal["build", "vote_session", "starboard_entry"]
"""What a post renders. One kind per renderer registered with the reconciler."""

Surface = Literal["build_card", "build_review", "vote_card", "starboard_entry"]
"""Which presentation a post uses. Several surfaces can render the same resource kind.

`build_review` and `vote_card` are both vote sessions: a review embeds the build being
voted on, while a delete-log vote or a generic poll stands alone.
"""


@dataclass(frozen=True, slots=True)
class PostTarget:
    """Where a post should exist, before it has been sent."""

    channel_id: int
    guild_id: int
    surface: Surface


@dataclass(frozen=True, slots=True)
class DiscordPost:
    """A Discord message the bot owns and keeps rendered.

    `channel_id` is duplicated from the message fact on purpose: "one post per
    resource per channel" is a unique constraint, and PostgreSQL cannot build a
    unique index across a join.
    """

    message_id: int
    channel_id: int
    resource_kind: ResourceKind
    resource_key: str
    surface: Surface
    applied_revision: int
    posted_at: Instant | None = None
    rendered_at: Instant | None = None
    suppressed_at: Instant | None = None
    """Set when someone deleted the post by hand. Renderers decide whether it returns."""

    @property
    def is_live(self) -> bool:
        """Whether this post should still be edited rather than replaced."""
        return self.suppressed_at is None


@dataclass(frozen=True, slots=True)
class PostReference:
    """What a Discord message id resolves to, for routing reactions and edits."""

    message_id: int
    resource_kind: ResourceKind
    resource_key: str
    surface: Surface
