"""Starboard snapshots and pure scoring rules."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal

from whenever import Instant

from squid.reactions.domain import ReactionActor

type StarboardDirection = Literal["up", "down"]
type VoteVerdictAction = Literal["accept", "ignore", "remove_reaction"]


@dataclass(frozen=True, slots=True)
class StarboardEmoji:
    """An ordered weighted reaction configured for a starboard."""

    emoji: str
    direction: StarboardDirection
    multiplier: float = 1.0
    position: int = 0

    def __post_init__(self) -> None:
        if not self.emoji.strip():
            msg = "Starboard emoji cannot be empty."
            raise ValueError(msg)
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Starboard emoji multiplier must be finite and greater than zero."
            raise ValueError(msg)
        if self.position < 0:
            msg = "Starboard emoji position cannot be negative."
            raise ValueError(msg)


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
            msg = "Starboard name cannot be empty."
            raise ValueError(msg)
        if not isfinite(self.required) or not isfinite(self.required_remove):
            msg = "Starboard thresholds must be finite."
            raise ValueError(msg)
        if self.required <= self.required_remove:
            msg = "The post threshold must exceed the removal threshold."
            raise ValueError(msg)
        if self.min_age_seconds < 0 or self.max_age_seconds < 0:
            msg = "Starboard message ages cannot be negative."
            raise ValueError(msg)
        if self.max_age_seconds and self.min_age_seconds > self.max_age_seconds:
            msg = "The minimum message age cannot exceed the maximum."
            raise ValueError(msg)
        if not 0 <= self.colour <= 0xFFFFFF:
            msg = "Starboard colour must be a valid RGB value."
            raise ValueError(msg)
        if not self.display_emoji.strip():
            msg = "Starboard display emoji cannot be empty."
            raise ValueError(msg)
        aliases = [item.emoji for item in self.emojis]
        if len(aliases) != len(set(aliases)):
            msg = "Starboard emojis must be unique."
            raise ValueError(msg)


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
    posted_message_id: int | None = None
    posted_channel_id: int | None = None
    last_rendered_score: float | None = None
    first_posted_at: Instant | None = None
    updated_at: Instant | None = None

    @property
    def posted(self) -> bool:
        return self.posted_message_id is not None


@dataclass(frozen=True, slots=True)
class VoteVerdict:
    """The policy result for one reaction."""

    action: VoteVerdictAction
    direction: StarboardDirection | None = None

    @classmethod
    def accept(cls, direction: StarboardDirection) -> "VoteVerdict":
        return cls("accept", direction)

    @classmethod
    def ignore(cls) -> "VoteVerdict":
        return cls("ignore")

    @classmethod
    def remove_reaction(cls) -> "VoteVerdict":
        return cls("remove_reaction")


class EntryAction(StrEnum):
    """A transport operation required for a materialized starboard entry."""

    SEND = "send"
    UPDATE = "update"
    REMOVE = "remove"
    NOOP = "noop"


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


def decide_entry_action(
    config: StarboardConfig,
    entry: StarboardEntry,
    score: float,
    origin_present: bool,
) -> EntryAction:
    """Choose a post action with a stable hysteresis band."""
    if not origin_present:
        return EntryAction.REMOVE if config.link_deletes and entry.posted else EntryAction.NOOP
    if score <= config.required_remove:
        return EntryAction.REMOVE if entry.posted else EntryAction.NOOP
    if score >= config.required:
        return EntryAction.UPDATE if entry.posted else EntryAction.SEND
    return EntryAction.UPDATE if entry.posted else EntryAction.NOOP
