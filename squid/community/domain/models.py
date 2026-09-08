"""Community automation policies and decisions."""

from dataclasses import dataclass
from enum import Enum, auto

from squid.core.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class RedstonerPolicy:
    """Configuration for granting the redstoner role from starboard posts."""

    starboard_author_id: int
    starboard_channel_id: int


class RedstonerDecisionKind(Enum):
    """IGNORE for a post from elsewhere, MALFORMED for one the starboard format cannot be read from, GRANT to act."""

    IGNORE = auto()
    MALFORMED = auto()
    GRANT = auto()


@dataclass(frozen=True, slots=True)
class RedstonerDecision:
    """The verdict on one starboard post; `member_id` and `source_message_url` are set only when the kind is GRANT."""

    kind: RedstonerDecisionKind
    member_id: int | None = None
    source_message_url: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WelcomeRelayPolicy:
    """Configuration for forwarding Discord welcome messages.

    Raises `ConfigurationError` unless `forward_chance` is between zero and one and the other two are positive.
    """

    welcome_channel_id: int
    forward_chance: float
    """Probability that an eligible welcome message is forwarded at all."""
    pending_ttl_seconds: float = 300
    """How long after joining a member can still be matched to a welcome message."""
    max_pending_members: int = 100
    """Cap on remembered joins; the oldest are dropped first."""

    def __post_init__(self) -> None:
        if not 0 <= self.forward_chance <= 1:
            msg = "forward_chance must be between zero and one"
            raise ConfigurationError(msg, context={"field": "forward_chance"})
        if self.pending_ttl_seconds <= 0:
            msg = "pending_ttl_seconds must be positive"
            raise ConfigurationError(msg, context={"field": "pending_ttl_seconds"})
        if self.max_pending_members <= 0:
            msg = "max_pending_members must be positive"
            raise ConfigurationError(msg, context={"field": "max_pending_members"})


@dataclass(frozen=True, slots=True)
class PendingWelcomeMember:
    """A recently joined member that may appear in a system welcome message."""

    user_id: int
    username: str
    joined_at: float
    """Monotonic clock reading, not a wall-clock time; only differences are meaningful."""


@dataclass(frozen=True, slots=True)
class WelcomeRelayDecision:
    """A resolved member mention for a welcome message."""

    member_id: int
    matched_name: str
