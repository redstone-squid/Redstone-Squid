"""Community automation application services."""

import re
import time
from collections.abc import Callable
from random import Random

from squid.community.domain import (
    PendingWelcomeMember,
    RedstonerDecision,
    RedstonerDecisionKind,
    RedstonerPolicy,
    WelcomeRelayDecision,
    WelcomeRelayPolicy,
)


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
        """Decide whether the post grants the role.

        Ignores anything not from the configured starboard author and channel; a post from there that does not
        carry exactly one mention and a message link is MALFORMED rather than ignored.
        """
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


class WelcomeRelayService:
    """Track recent joins in memory and match them to Discord's system welcome messages.

    State is per process and unbounded only by the policy's TTL and member cap; a restart forgets every join.
    """

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
        """Record a join, dropping entries past the TTL and then the oldest beyond the member cap."""
        now = self._clock()
        self._prune(now)
        self._pending_members.append(PendingWelcomeMember(user_id, username, now))
        excess = len(self._pending_members) - self._policy.max_pending_members
        if excess > 0:
            del self._pending_members[:excess]

    def should_consider(self, *, channel_id: int, is_new_member_message: bool) -> bool:
        """Whether to forward this welcome event; draws on the policy's chance, so it is not a pure predicate."""
        return (
            channel_id == self._policy.welcome_channel_id
            and is_new_member_message
            and self._random.random() < self._policy.forward_chance
        )

    def resolve(self, system_content: str) -> WelcomeRelayDecision | None:
        """Consume and return the one tracked member named in the message, or `None` if zero or several match."""
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
