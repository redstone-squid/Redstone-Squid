from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from whenever import Instant

from squid.core.actors import Actor
from squid.messages.models import Message
from squid.starboard.domain import entry_should_be_posted, evaluate_vote
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEmoji

NOW = Instant.parse_iso("2026-08-04T12:00:00Z")


def config(**changes: object) -> StarboardConfig:
    return replace(
        StarboardConfig(
            id=1,
            guild_id=10,
            channel_id=20,
            name="main",
            emojis=(StarboardEmoji(emoji="⭐", direction="up"), StarboardEmoji(emoji="💩", direction="down")),
        ),
        **changes,  # pyrefly: ignore[bad-argument-type]
    )


def origin(*, deleted_at: Instant | None = None, **changes: object) -> OriginMessage:
    result = replace(
        OriginMessage(
            id=100,
            author_is_bot=False,
            message=Message(id=100, guild_id=10, channel_id=30, author_id=40, created_at=NOW.subtract(seconds=60)),
        ),
        **changes,  # pyrefly: ignore[bad-argument-type]
    )

    result.message.deleted_at = deleted_at
    return result


@pytest.mark.parametrize(
    ("changes", "actor", "expected"),
    [
        ({}, Actor(discord_user_id=40), "remove_reaction"),
        ({"self_vote": True}, Actor(discord_user_id=40), "accept"),
        ({"allow_bots": False}, Actor(discord_user_id=41), "accept"),
        ({"require_image": True}, Actor(discord_user_id=41), "remove_reaction"),
        ({"min_age_seconds": 61}, Actor(discord_user_id=41), "remove_reaction"),
        ({"max_age_seconds": 59}, Actor(discord_user_id=41), "remove_reaction"),
    ],
)
def test_vote_policy(changes: dict[str, object], actor: Actor, expected: str) -> None:
    assert evaluate_vote(config(**changes), origin(), actor, "⭐", now=NOW).action == expected


def test_vote_policy_rejects_bot_authors_and_accepts_age_boundaries() -> None:
    assert (
        evaluate_vote(config(), origin(author_is_bot=True), Actor(discord_user_id=41), "⭐", now=NOW).action
        == "remove_reaction"
    )
    bounded = config(min_age_seconds=60, max_age_seconds=60)
    assert evaluate_vote(bounded, origin(), Actor(discord_user_id=41), "⭐", now=NOW).action == "accept"


def test_unconfigured_reactions_and_deleted_origins_are_ignored() -> None:
    assert evaluate_vote(config(), origin(), Actor(discord_user_id=41), "other", now=NOW).action == "ignore"
    assert evaluate_vote(config(), origin(deleted_at=NOW), Actor(discord_user_id=41), "⭐", now=NOW).action == "ignore"


@pytest.mark.parametrize(
    ("score", "posted", "expected"),
    [
        (-1, False, False),
        (0, True, False),
        # Between required_remove and required nothing changes either way, which is
        # what stops an entry at the boundary flickering in and out of the channel.
        (0.1, False, False),
        (0.1, True, True),
        (3, False, True),
        (3, True, True),
    ],
)
def test_entry_hysteresis(score: float, posted: bool, expected: bool) -> None:
    assert entry_should_be_posted(config(), score, origin_present=True, currently_posted=posted) is expected


def test_origin_delete_only_removes_mirrors_on_boards_that_link_deletes() -> None:
    assert entry_should_be_posted(config(), 5, origin_present=False, currently_posted=True) is False
    assert entry_should_be_posted(config(link_deletes=False), 5, origin_present=False, currently_posted=True) is True
    assert entry_should_be_posted(config(), 5, origin_present=False, currently_posted=False) is False


@given(st.lists(st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False), min_size=1))
def test_increasing_scores_never_remove_after_send(scores: list[float]) -> None:
    """Once posted, a rising score can never take the mirror back down."""
    posted = False
    for score in sorted(scores):
        wanted = entry_should_be_posted(config(), score, origin_present=True, currently_posted=posted)
        assert not (posted and not wanted)
        posted = wanted


def test_fractional_weights_reach_the_threshold() -> None:
    """Ten votes weighted 0.1 must post on a board that requires one point."""
    score = 0.0
    for _ in range(10):
        score += 0.1  # 0.9999999999999999, as PostgreSQL's float SUM returns it
    assert entry_should_be_posted(config(required=1), score, origin_present=True, currently_posted=False)
