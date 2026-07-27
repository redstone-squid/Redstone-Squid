"""Framework-neutral policies for Discord community automations."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from random import Random


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


class RedstonerService:
    """Decide whether a starboard post should grant the redstoner role."""

    _message_link_pattern = re.compile(r"https://discord\.com/channels/\d+/\d+/\d+")

    def __init__(self, policy: RedstonerPolicy):
        self._policy = policy

    def evaluate(
        self,
        *,
        author_id: int,
        channel_id: int,
        mentioned_user_ids: list[int],
        content: str,
    ) -> RedstonerDecision:
        """Evaluate a starboard post without depending on Discord objects."""
        if author_id != self._policy.starboard_author_id or channel_id != self._policy.starboard_channel_id:
            return RedstonerDecision(RedstonerDecisionKind.IGNORE)

        if len(mentioned_user_ids) != 1:
            return RedstonerDecision(
                RedstonerDecisionKind.MALFORMED,
                reason=f"Expected 1 mention from starboard, got {len(mentioned_user_ids)}",
            )

        match = self._message_link_pattern.search(content)
        if match is None:
            return RedstonerDecision(
                RedstonerDecisionKind.MALFORMED,
                reason="Starboard post does not contain a Discord message link",
            )

        return RedstonerDecision(
            RedstonerDecisionKind.GRANT,
            member_id=mentioned_user_ids[0],
            source_message_url=match.group(0),
        )


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
            raise ValueError(msg)
        if self.pending_ttl_seconds <= 0:
            msg = "pending_ttl_seconds must be positive"
            raise ValueError(msg)
        if self.max_pending_members <= 0:
            msg = "max_pending_members must be positive"
            raise ValueError(msg)


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


class WelcomeRelayService:
    """Track recent members and resolve welcome-message relay decisions."""

    def __init__(
        self,
        policy: WelcomeRelayPolicy,
        *,
        random_source: Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._policy = policy
        self._random = random_source or Random()
        self._clock = clock
        self._pending_members: list[PendingWelcomeMember] = []

    def record_join(self, user_id: int, username: str) -> None:
        """Record a recent member join, pruning stale and excess state."""
        now = self._clock()
        self._prune(now)
        self._pending_members.append(PendingWelcomeMember(user_id, username, now))
        excess = len(self._pending_members) - self._policy.max_pending_members
        if excess > 0:
            del self._pending_members[:excess]

    def should_consider(self, *, channel_id: int, is_new_member_message: bool) -> bool:
        """Return whether a welcome event should be considered for forwarding."""
        return (
            channel_id == self._policy.welcome_channel_id
            and is_new_member_message
            and self._random.random() < self._policy.forward_chance
        )

    def resolve(self, system_content: str) -> WelcomeRelayDecision | None:
        """Resolve and consume the single recent member named in a welcome message."""
        self._prune(self._clock())
        matches = [member for member in self._pending_members if member.username in system_content]
        if len(matches) != 1:
            return None

        member = matches[0]
        self._pending_members.remove(member)
        return WelcomeRelayDecision(member.user_id, member.username)

    def _prune(self, now: float) -> None:
        cutoff = now - self._policy.pending_ttl_seconds
        self._pending_members = [member for member in self._pending_members if member.joined_at >= cutoff]
