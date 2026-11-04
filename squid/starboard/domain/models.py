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
"""Settings accepted by the configuration form, with their value parsers.

The form's choices and the service's update allowlist share this registry.
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
    """Evaluate one board's policy against the supplied message and actor facts.

    `allow_bots` concerns the message author; transport adapters reject bot voters.
    A removal verdict is a recommendation: the caller applies
    `remove_invalid_reactions` before removing anything from Discord.
    """
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
    """Return whether the score warrants a post, retaining its state between thresholds.

    At or below `required_remove`, remove the post. At or above `required`, create
    it. Between those bounds, preserve `currently_posted`. If the origin is gone,
    retain an existing post only when `link_deletes` is disabled; this decision
    does not establish whether Discord can still supply content for rendering.
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
