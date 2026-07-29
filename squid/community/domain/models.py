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
    """Possible outcomes when evaluating a starboard post."""

    IGNORE = auto()
    MALFORMED = auto()
    GRANT = auto()


@dataclass(frozen=True, slots=True)
class RedstonerDecision:
    """Result of evaluating a starboard post."""

    kind: RedstonerDecisionKind
    member_id: int | None = None
    source_message_url: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WelcomeRelayPolicy:
    """Configuration for forwarding Discord welcome messages."""

    welcome_channel_id: int
    forward_chance: float
    pending_ttl_seconds: float = 300
    max_pending_members: int = 100

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


@dataclass(frozen=True, slots=True)
class WelcomeRelayDecision:
    """A resolved member mention for a welcome message."""

    member_id: int
    matched_name: str
