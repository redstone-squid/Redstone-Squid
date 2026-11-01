"""Starboard snapshots and pure scoring rules."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from whenever import Instant

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
    """Authorize a configured reaction using the loaded configuration and message facts."""
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


if TYPE_CHECKING:
    from squid.starboard.models import OriginMessage, StarboardConfig
