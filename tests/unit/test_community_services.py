from random import Random

import pytest

from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerDecisionKind, RedstonerPolicy, WelcomeRelayPolicy
from squid.core.errors import ConfigurationError


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


def test_welcome_relay_consumes_unique_match() -> None:
    now = 10.0
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1),
        random_source=Random(0),
        clock=lambda: now,
    )
    service.record_join(2, "Alice")

    assert service.should_consider(channel_id=1, is_new_member_message=True)
    assert service.resolve("Welcome Alice!") is not None
    assert service.resolve("Welcome Alice!") is None


def test_welcome_relay_rejects_ambiguous_and_expired_matches() -> None:
    now = 10.0
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=1, forward_chance=1, pending_ttl_seconds=5),
        clock=lambda: now,
    )
    service.record_join(2, "Alex")
    service.record_join(3, "Alex")

    assert service.resolve("Welcome Alex!") is None

    now = 20.0
    assert service.resolve("Welcome Alex!") is None
