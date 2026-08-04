from dataclasses import replace
from math import inf, nan

import pytest
from whenever import Instant

from squid.bot.voting.generic_session import GenericVoteSession
from squid.bot.voting.poll_wizard import parse_poll_duration
from squid.voting.application import RoleVoteWeightPolicy
from squid.voting.domain import (
    GenericPoll,
    RoleWeight,
    VoteActor,
    VoteChoice,
    VoteOption,
    VoteSelection,
    VoteSessionSnapshot,
    VoteTarget,
    normalize_vote_options,
)
from squid.voting.errors import InvalidVoteConfigurationError


def generic_snapshot(*, selections: tuple[VoteSelection, ...] = ()) -> VoteSessionSnapshot:
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=10, label="One"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=10, label="Two"),
    )
    return VoteSessionSnapshot(
        id=1,
        author_id=2,
        kind="generic",
        status="open",
        result="pending",
        pass_threshold=32767,
        fail_threshold=-32768,
        votes={},
        messages=(),
        options=options,
        target=VoteTarget(),
        selections=selections,
        poll=GenericPoll("Question?", "anonymous_live", 10, Instant.now().add(hours=1)),
    )


async def test_role_policy_uses_highest_matching_multiplier_and_staff_fallback() -> None:
    async def weights(guild_id: int, kind: str) -> tuple[RoleWeight, ...]:
        assert (guild_id, kind) == (10, "generic")
        return (RoleWeight(10, "generic", 20, 1.5), RoleWeight(10, "generic", 30, 2.5))

    policy = RoleVoteWeightPolicy(weights)
    snapshot = generic_snapshot()

    assert await policy.calculate(VoteActor(1, 10, frozenset({20, 30})), snapshot, "1️⃣") == 2.5
    assert await policy.calculate(VoteActor(1, 10, is_staff=True), snapshot, "1️⃣") == 3
    assert await policy.calculate(VoteActor(1, 10), snapshot, "1️⃣") == 1


async def test_delete_policy_rejects_untrusted_member() -> None:
    async def no_weights(guild_id: int, kind: str) -> tuple[RoleWeight, ...]:
        return ()

    policy = RoleVoteWeightPolicy(no_weights)
    snapshot = generic_snapshot()
    snapshot = VoteSessionSnapshot(
        id=snapshot.id,
        author_id=snapshot.author_id,
        kind="delete_log",
        status=snapshot.status,
        result=snapshot.result,
        pass_threshold=3,
        fail_threshold=-3,
        votes={},
        messages=(),
        options=(VoteOption("👍", VoteChoice.APPROVE), VoteOption("👎", VoteChoice.DENY)),
        target=VoteTarget(),
    )

    assert await policy.calculate(VoteActor(1, 10), snapshot, "👍") is None
    assert await policy.calculate(VoteActor(1, 10, is_trusted=True), snapshot, "👍") == 1


def test_generic_tallies_keep_raw_counts_separate_from_weights() -> None:
    snapshot = generic_snapshot(
        selections=(
            VoteSelection(1, 10, "one", "1️⃣", 1),
            VoteSelection(2, 10, "one", "1️⃣", 3),
            VoteSelection(3, 10, "two", "2️⃣", 2),
        )
    )

    assert snapshot.raw_tallies() == {"one": 2, "two": 1}
    assert snapshot.weighted_tallies() == {"one": 4, "two": 2}


def test_hidden_poll_suppresses_live_totals_and_reports_tie_at_close() -> None:
    selections = (
        VoteSelection(1, 10, "one", "1️⃣", 2),
        VoteSelection(2, 10, "two", "2️⃣", 2),
    )
    snapshot = generic_snapshot(selections=selections)
    assert snapshot.poll is not None
    hidden = replace(snapshot, poll=replace(snapshot.poll, visibility="anonymous_hidden"))

    live = GenericVoteSession(None, hidden).render()  # type: ignore[arg-type]
    closed = GenericVoteSession(None, replace(hidden, status="closed", result="cancelled")).render()  # type: ignore[arg-type]

    assert "votes" not in live
    assert "Tie: One, Two" in closed


def test_visible_poll_lists_voters() -> None:
    snapshot = generic_snapshot(selections=(VoteSelection(42, 10, "one", "1️⃣", 1),))
    assert snapshot.poll is not None
    visible = replace(snapshot, poll=replace(snapshot.poll, visibility="visible_live"))

    assert "<@42>" in GenericVoteSession(None, visible).render()  # type: ignore[arg-type]


def test_binary_aliases_may_repeat_choice_but_not_emoji_per_guild() -> None:
    assert (
        len(
            normalize_vote_options(
                (
                    VoteOption("👍", VoteChoice.APPROVE),
                    VoteOption("✅", VoteChoice.APPROVE),
                    VoteOption("👎", VoteChoice.DENY),
                )
            )
        )
        == 3
    )
    with pytest.raises(InvalidVoteConfigurationError, match="unique"):
        normalize_vote_options((VoteOption("👍", VoteChoice.APPROVE), VoteOption("👍", VoteChoice.DENY)))


@pytest.mark.parametrize("multiplier", [0, -1, inf, nan])
def test_role_weights_must_be_positive_and_finite(multiplier: float) -> None:
    with pytest.raises(InvalidVoteConfigurationError, match="finite"):
        RoleWeight(10, "build", 20, multiplier)


@pytest.mark.parametrize(("value", "seconds"), [("1m", 60), ("24h", 86400), ("30d", 2592000)])
def test_poll_duration_parser(value: str, seconds: int) -> None:
    assert parse_poll_duration(value) == seconds


@pytest.mark.parametrize("value", ["59s", "0m", "31d", "forever"])
def test_poll_duration_parser_rejects_out_of_range_values(value: str) -> None:
    with pytest.raises(InvalidVoteConfigurationError):
        parse_poll_duration(value)
