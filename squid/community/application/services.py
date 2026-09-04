"""Community automation application services."""

import re
import time
from collections.abc import Callable
from random import Random

from squid.community.domain import (
    PendingWelcomeMember,
    PendingWelcomeMessage,
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


class WelcomeRelayService:
    """Correlate recent member joins and welcome messages without waiting."""

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
        self._pending_messages: list[PendingWelcomeMessage] = []

    def record_join(self, user_id: int, username: str) -> WelcomeRelayDecision | None:
        """Record a member join and return a decision when its message arrived first."""
        now = self._clock()
        self._prune(now)
        self._pending_members.append(PendingWelcomeMember(user_id, username, now))
        self._trim(self._pending_members)

        matching_messages = [message for message in self._pending_messages if username in message.system_content]
        if len(matching_messages) != 1:
            return None
        message = matching_messages[0]
        decision = self._resolve(message)
        if decision is not None:
            self._pending_messages.remove(message)
        return decision

    def record_message(
        self,
        *,
        channel_id: int,
        is_new_member_message: bool,
        system_content: str,
    ) -> WelcomeRelayDecision | None:
        """Record one eligible message and return a decision when its join arrived first."""
        if channel_id != self._policy.welcome_channel_id or not is_new_member_message:
            return None
        if self._random.random() >= self._policy.forward_chance:
            return None

        now = self._clock()
        self._prune(now)
        message = PendingWelcomeMessage(system_content, now)
        decision = self._resolve(message)
        if decision is not None:
            return decision
        self._pending_messages.append(message)
        self._trim(self._pending_messages)
        return None

    def _resolve(self, message: PendingWelcomeMessage) -> WelcomeRelayDecision | None:
        matches = [member for member in self._pending_members if member.username in message.system_content]
        if len(matches) != 1:
            return None

        member = matches[0]
        self._pending_members.remove(member)
        return WelcomeRelayDecision(member.user_id, member.username, message.system_content)

    def _trim[T](self, pending: list[T]) -> None:
        excess = len(pending) - self._policy.max_pending_members
        if excess > 0:
            del pending[:excess]

    def _prune(self, now: float) -> None:
        cutoff = now - self._policy.pending_ttl_seconds
        self._pending_members = [member for member in self._pending_members if member.joined_at >= cutoff]
        self._pending_messages = [message for message in self._pending_messages if message.received_at >= cutoff]
