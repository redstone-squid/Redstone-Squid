"""Starboard snapshots and pure scoring rules."""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from whenever import Instant

from squid.core.errors import ValidationError
from squid.core.i18n import _
from squid.reactions.domain import ReactionActor

type StarboardDirection = Literal["up", "down"]
type VoteVerdictAction = Literal["accept", "ignore", "remove_reaction"]
type SettingKind = Literal["boolean", "threshold", "integer", "text"]

EDITABLE_SETTINGS: dict[str, SettingKind] = {
    "enabled": "boolean",
    "self_vote": "boolean",
    "allow_bots": "boolean",
    "require_image": "boolean",
    "autoreact_upvote": "boolean",
    "autoreact_downvote": "boolean",
    "remove_invalid_reactions": "boolean",
    "link_edits": "boolean",
    "link_deletes": "boolean",
    "jump_to_message": "boolean",
    "attachments_list": "boolean",
    "replied_to": "boolean",
    "ping_author": "boolean",
    "required": "threshold",
    "required_remove": "threshold",
    "min_age_seconds": "integer",
    "max_age_seconds": "integer",
    "colour": "integer",
    "channel_id": "integer",
    "name": "text",
    "display_emoji": "text",
}
"""Every setting `/starboard edit` accepts, and how its value is parsed.

Shared with the suggestion registry so the names a user can be offered and the names the command
will actually accept cannot drift apart.
"""


@dataclass(frozen=True, slots=True)
class StarboardEmoji:
    """An ordered weighted reaction configured for a starboard."""

    emoji: str
    direction: StarboardDirection
    multiplier: float = 1.0
    position: int = 0

    def __post_init__(self) -> None:
        if not self.emoji.strip():
            msg = _("Starboard emoji cannot be empty.")
            raise ValidationError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = _("Starboard emoji multiplier must be finite and greater than zero.")
            raise ValidationError(msg)
        if self.position < 0:
            msg = _("Starboard emoji position cannot be negative.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class StarboardConfig:
    """A complete immutable starboard configuration snapshot."""

    id: int
    guild_id: int
    channel_id: int
    name: str
    emojis: tuple[StarboardEmoji, ...]
    enabled: bool = True
    required: float = 3.0
    required_remove: float = 0.0
    self_vote: bool = False
    allow_bots: bool = False
    require_image: bool = False
    min_age_seconds: int = 0
    max_age_seconds: int = 0
    autoreact_upvote: bool = True
    autoreact_downvote: bool = True
    remove_invalid_reactions: bool = False
    link_edits: bool = True
    link_deletes: bool = True
    display_emoji: str = "⭐"
    colour: int = 0x435E81
    jump_to_message: bool = True
    attachments_list: bool = True
    replied_to: bool = True
    ping_author: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = _("Starboard name cannot be empty.")
            raise ValidationError(msg)
        if not isfinite(self.required) or not isfinite(self.required_remove):
            msg = _("Starboard thresholds must be finite.")
            raise ValidationError(msg)
        if self.required <= self.required_remove:
            msg = _("The post threshold must exceed the removal threshold.")
            raise ValidationError(msg)
        if self.min_age_seconds < 0 or self.max_age_seconds < 0:
            msg = _("Starboard message ages cannot be negative.")
            raise ValidationError(msg)
        if self.max_age_seconds and self.min_age_seconds > self.max_age_seconds:
            msg = _("The minimum message age cannot exceed the maximum.")
            raise ValidationError(msg)
        if not 0 <= self.colour <= 0xFFFFFF:
            msg = _("Starboard colour must be a valid RGB value.")
            raise ValidationError(msg)
        if not self.display_emoji.strip():
            msg = _("Starboard display emoji cannot be empty.")
            raise ValidationError(msg)
        aliases = [item.emoji for item in self.emojis]
        if len(aliases) != len(set(aliases)):
            msg = _("Starboard emojis must be unique.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class StarboardSource:
    """A guild or channel source grant for a starboard."""

    starboard_id: int
    guild_id: int
    channel_id: int = 0
    approved_by: int | None = None
    approved_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class OriginMessage:
    """Persisted source-message facts used by starboard policy."""

    id: int
    guild_id: int
    channel_id: int
    author_id: int
    author_is_bot: bool
    posted_at: Instant
    is_nsfw: bool = False
    has_image: bool = False
    deleted_at: Instant | None = None

    @property
    def present(self) -> bool:
        return self.deleted_at is None


@dataclass(frozen=True, slots=True)
class StarboardVote:
    """One actor's current reaction to an origin message."""

    starboard_id: int
    origin_message_id: int
    user_id: int
    emoji: str
    direction: StarboardDirection
    weight: float
    target_author_id: int
    created_at: Instant


@dataclass(frozen=True, slots=True)
class StarboardEntry:
    """Persisted materialization state for an origin on a starboard."""

    starboard_id: int
    origin_message_id: int
    score: float = 0.0
    raw_count: int = 0
    last_rendered_score: float | None = None
    first_posted_at: Instant | None = None
    updated_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class VoteVerdict:
    """The policy result for one reaction."""

    action: VoteVerdictAction
    direction: StarboardDirection | None = None

    @classmethod
    def accept(cls, direction: StarboardDirection) -> VoteVerdict:
        return cls("accept", direction)

    @classmethod
    def ignore(cls) -> VoteVerdict:
        return cls("ignore")

    @classmethod
    def remove_reaction(cls) -> VoteVerdict:
        return cls("remove_reaction")


def evaluate_vote(
    config: StarboardConfig,
    origin: OriginMessage,
    actor: ReactionActor,
    emoji: str,
    *,
    now: Instant | None = None,
) -> VoteVerdict:
    """Authorize a configured reaction using only immutable snapshots."""
    option = next((item for item in config.emojis if item.emoji == emoji), None)
    if option is None or not config.enabled or not origin.present:
        return VoteVerdict.ignore()
    if (not config.self_vote and actor.user_id == origin.author_id) or (not config.allow_bots and origin.author_is_bot):
        return VoteVerdict.remove_reaction()
    if config.require_image and not origin.has_image:
        return VoteVerdict.remove_reaction()
    age = (now or Instant.now()).difference(origin.posted_at).total("seconds")
    if age < config.min_age_seconds or (config.max_age_seconds and age > config.max_age_seconds):
        return VoteVerdict.remove_reaction()
    return VoteVerdict.accept(option.direction)


def entry_should_be_posted(
    config: StarboardConfig,
    score: float,
    *,
    origin_present: bool,
    currently_posted: bool,
) -> bool:
    """Whether this entry warrants a post right now, with a stable hysteresis band.

    Replaces a four-way send/update/remove/noop decision. Those verbs described what
    to *do* about a post, which only made sense while the entry row remembered whether
    one existed; the reconciler compares this answer against the posts that are
    actually there and works out the verb itself.
    """
    if not origin_present:
        # A deleted source removes its mirror only where the board links deletions.
        return currently_posted and not config.link_deletes
    if score <= config.required_remove:
        return False
    if score >= config.required:
        return True
    # Between the two thresholds nothing changes, which is what stops an entry
    # hovering at the boundary from flickering in and out of the channel.
    return currently_posted
