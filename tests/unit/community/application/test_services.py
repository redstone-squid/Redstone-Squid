"""Community application service tests."""

from random import Random
from typing import override

import pytest

from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerDecisionKind, RedstonerPolicy, WelcomeRelayDecision, WelcomeRelayPolicy
from squid.core.errors import ConfigurationError


class SequenceRandom(Random):
    """Return a finite sequence while exposing how often policy was rolled."""

    def __init__(self, *values: float) -> None:
        super().__init__(0)
        self.values = iter(values)
        self.calls = 0

    @override
    def random(self) -> float:
        self.calls += 1
        return next(self.values)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("forward_chance", "pending_ttl_seconds", "max_pending_members", "field"),
    [
        (2, 300, 100, "forward_chance"),
        (1, 0, 100, "pending_ttl_seconds"),
        (1, 300, 0, "max_pending_members"),
    ],
)
def test_welcome_relay_rejects_invalid_configuration(
    forward_chance: float,
    pending_ttl_seconds: float,
    max_pending_members: int,
    field: str,
) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        WelcomeRelayPolicy(
            welcome_channel_id=1,
            forward_chance=forward_chance,
            pending_ttl_seconds=pending_ttl_seconds,
            max_pending_members=max_pending_members,
        )

    assert exc_info.value.context == {"field": field}


def test_redstoner_ignores_unrelated_posts() -> None:
    service = RedstonerService(RedstonerPolicy(starboard_author_id=1, starboard_channel_id=2))

    result = service.evaluate(author_id=9, channel_id=2, mentioned_user_ids=[3], content="")

    assert result.kind is RedstonerDecisionKind.IGNORE


def test_redstoner_rejects_missing_message_link() -> None:
    service = RedstonerService(RedstonerPolicy(starboard_author_id=1, starboard_channel_id=2))

    result = service.evaluate(author_id=1, channel_id=2, mentioned_user_ids=[3], content="No link")

    assert result.kind is RedstonerDecisionKind.MALFORMED
    assert result.reason == "Starboard post does not contain a Discord message link"


def test_redstoner_grants_valid_post() -> None:
    service = RedstonerService(RedstonerPolicy(starboard_author_id=1, starboard_channel_id=2))

    result = service.evaluate(
        author_id=1,
        channel_id=2,
        mentioned_user_ids=[3],
        content="See https://discord.com/channels/4/5/6",
    )

    assert result.kind is RedstonerDecisionKind.GRANT
    assert result.member_id == 3
    assert result.source_message_url == "https://discord.com/channels/4/5/6"


@pytest.mark.parametrize("message_first", [False, True])
def test_welcome_relay_resolves_both_event_orders(message_first: bool) -> None:
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1),
        random_source=Random(0),
    )
    message = {
        "channel_id": 1,
        "is_new_member_message": True,
        "system_content": "Welcome Alice!",
    }

    if message_first:
        assert service.record_message(**message) is None
        decision = service.record_join(2, "Alice")
    else:
        assert service.record_join(2, "Alice") is None
        decision = service.record_message(**message)

    assert decision == WelcomeRelayDecision(2, "Alice", "Welcome Alice!")


def test_welcome_relay_rolls_once_and_parks_only_messages_that_win() -> None:
    random_source = SequenceRandom(0.25, 0.75)
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=0.5),
        random_source=random_source,
    )

    assert service.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alice!") is None
    assert service.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Bob!") is None
    assert service.record_join(3, "Bob") is None
    decision = service.record_join(2, "Alice")

    assert random_source.calls == 2
    assert decision == WelcomeRelayDecision(2, "Alice", "Welcome Alice!")


def test_welcome_relay_does_not_roll_for_unrelated_messages() -> None:
    random_source = SequenceRandom()
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1),
        random_source=random_source,
    )

    assert service.record_message(channel_id=2, is_new_member_message=True, system_content="Welcome Alice!") is None
    assert service.record_message(channel_id=1, is_new_member_message=False, system_content="Welcome Alice!") is None

    assert random_source.calls == 0


@pytest.mark.parametrize("message_first", [False, True])
def test_welcome_relay_expires_either_side_of_an_unmatched_pair(message_first: bool) -> None:
    clock = Clock(10)
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1, pending_ttl_seconds=5),
        random_source=Random(0),
        clock=clock,
    )

    if message_first:
        assert service.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alice!") is None
        clock.now = 20
        decision = service.record_join(2, "Alice")
    else:
        assert service.record_join(2, "Alice") is None
        clock.now = 20
        decision = service.record_message(
            channel_id=1,
            is_new_member_message=True,
            system_content="Welcome Alice!",
        )

    assert decision is None


def test_welcome_relay_applies_the_pending_bound_to_members_and_messages() -> None:
    policy = WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1, max_pending_members=1)
    message_first = WelcomeRelayService(policy, random_source=Random(0))
    assert (
        message_first.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alice!") is None
    )
    assert message_first.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Bob!") is None
    assert message_first.record_join(2, "Alice") is None
    assert message_first.record_join(3, "Bob") == WelcomeRelayDecision(3, "Bob", "Welcome Bob!")

    join_first = WelcomeRelayService(policy, random_source=Random(0))
    assert join_first.record_join(2, "Alice") is None
    assert join_first.record_join(3, "Bob") is None
    assert join_first.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alice!") is None
    assert join_first.record_message(
        channel_id=1, is_new_member_message=True, system_content="Welcome Bob!"
    ) == WelcomeRelayDecision(3, "Bob", "Welcome Bob!")


def test_welcome_relay_rejects_ambiguous_and_expired_matches() -> None:
    clock = Clock(10)
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1, pending_ttl_seconds=5),
        random_source=Random(0),
        clock=clock,
    )
    service.record_join(2, "Alex")
    service.record_join(3, "Alex")

    assert service.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alex!") is None

    clock.now = 20
    assert service.record_message(channel_id=1, is_new_member_message=True, system_content="Welcome Alex!") is None
