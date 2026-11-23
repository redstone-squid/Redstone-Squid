"""Authorization outcomes for changes to saved build facts."""

from collections.abc import Set
from enum import StrEnum

from squid.core.builds.domain.models import BuildStatus


class BuildEditDecision(StrEnum):
    DIRECT = "direct"
    REVIEW = "review"
    DENY = "deny"


_CREATOR_DIRECT = frozenset({"display_name", "extra_user_info", "showcase_tags"})
_MODERATOR_ONLY = frozenset({"credits", "creators_ign", "ai_generated", "extra_info"})


def decide_build_edit(
    fields: Set[str], *, status: BuildStatus | None, is_creator: bool, is_moderator: bool
) -> BuildEditDecision:
    """Require review for creator changes to published build facts beyond presentation fields."""
    if is_moderator:
        return BuildEditDecision.DIRECT
    if not is_creator or fields & _MODERATOR_ONLY:
        return BuildEditDecision.DENY
    if status is BuildStatus.PENDING or fields <= _CREATOR_DIRECT:
        return BuildEditDecision.DIRECT
    return BuildEditDecision.REVIEW
