from dataclasses import replace
from math import inf, nan

import pytest

from squid.bot.voting.poll_wizard import format_duration, parse_option_lines, parse_poll_duration
from squid.bot.voting.rendering import generic_poll_text
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_POLL_CLOSE_ANY, VOTE_WEIGHT_STAFF
from squid.voting.application import RoleVoteWeightPolicy
from squid.voting.domain import (
    PollScope,
    RoleWeight,
    VoteActor,
    VoteChoice,
    VoteKind,
    VoteOption,
    VoteRejection,
    VoteSelection,
    VoteSessionResult,
    VoteStatus,
    VoteVisibility,
    normalize_vote_options,
    validate_thresholds,
)
from squid.voting.errors import InvalidVoteConfigurationError
from tests.helpers.voting import build_snapshot, poll_snapshot

STAFF = frozenset({VOTE_WEIGHT_STAFF.name})
DELETE_LOG = frozenset({VOTE_LOG_DELETE_CAST.name})
CLOSE_ANY = frozenset({VOTE_POLL_CLOSE_ANY.name})


async def test_role_policy_uses_highest_matching_multiplier_and_staff_fallback() -> None:
    async def weights(guild_id: int, kind: str) -> tuple[RoleWeight, ...]:
        assert (guild_id, kind) == (10, VoteKind.GENERIC)
        return (
            RoleWeight(10, VoteKind.GENERIC, 20, 1.5),
            RoleWeight(10, VoteKind.GENERIC, 30, 2.5),
        )

    policy = RoleVoteWeightPolicy(weights)
    snapshot = poll_snapshot()

    assert await policy.calculate(VoteActor(1, 100, 10, frozenset({20, 30})), snapshot, "1️⃣") == 2.5
    assert await policy.calculate(VoteActor(1, 100, 10, capabilities=STAFF), snapshot, "1️⃣") == 3
    assert await policy.calculate(VoteActor(1, 100, 10), snapshot, "1️⃣") == 1


async def test_delete_policy_rejects_a_member_without_the_delete_log_node() -> None:
    async def no_weights(guild_id: int, kind: str) -> tuple[RoleWeight, ...]:
        return ()

    policy = RoleVoteWeightPolicy(no_weights)
    snapshot = build_snapshot(
        kind=VoteKind.DELETE_LOG,
        options=(VoteOption("👍", VoteChoice.APPROVE), VoteOption("👎", VoteChoice.DENY)),
        target=None,
    )

    assert await policy.calculate(VoteActor(1, 100, 10), snapshot, "👍") is None
    assert await policy.calculate(VoteActor(1, 100, 10, capabilities=DELETE_LOG), snapshot, "👍") == 1


def test_generic_tallies_keep_raw_counts_separate_from_weights() -> None:
    snapshot = poll_snapshot(
        selections=(
            VoteSelection(1, 10, "one", "1️⃣", 1),
            VoteSelection(2, 10, "one", "1️⃣", 3),
            VoteSelection(3, 10, "two", "2️⃣", 2),
        )
    )

    assert snapshot.raw_tallies() == {"one": 2, "two": 1}
    assert snapshot.weighted_tallies() == {"one": 4, "two": 2}


def test_hidden_poll_suppresses_live_totals_and_reports_tie_at_close() -> None:
    hidden = poll_snapshot(
        visibility=VoteVisibility.ANONYMOUS_HIDDEN,
        selections=(
            VoteSelection(1, 10, "one", "1️⃣", 2),
            VoteSelection(2, 10, "two", "2️⃣", 2),
        ),
    )
    closed = replace(hidden, status=VoteStatus.CLOSED, result=VoteSessionResult.CANCELLED)

    assert not hidden.shows_tallies
    assert closed.shows_tallies
    assert "votes" not in generic_poll_text(hidden)
    assert "Tie: One, Two" in generic_poll_text(closed)


def test_visible_poll_lists_voters_and_keeps_their_reactions() -> None:
    visible = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        selections=(VoteSelection(42, 10, "one", "1️⃣", 1),),
    )

    assert not visible.is_anonymous
    assert not visible.should_remove_reaction_on_cast()
    assert "<@420>" in generic_poll_text(visible, {42: 420})


@pytest.mark.parametrize(
    "visibility",
    [VoteVisibility.ANONYMOUS_LIVE, VoteVisibility.ANONYMOUS_HIDDEN],
)
def test_anonymous_polls_strip_the_voter_reaction(visibility: VoteVisibility) -> None:
    snapshot = poll_snapshot(
        visibility=visibility,
        selections=(VoteSelection(42, 10, "one", "1️⃣", 1),),
    )

    assert snapshot.is_anonymous
    assert snapshot.should_remove_reaction_on_cast()
    assert "<@420>" not in generic_poll_text(snapshot, {42: 420})


@pytest.mark.parametrize("kind", [VoteKind.BUILD, VoteKind.DELETE_LOG])
def test_threshold_kinds_never_carry_anonymity_metadata(kind: VoteKind) -> None:
    snapshot = build_snapshot(kind=kind, target=None)

    assert snapshot.visibility is None
    assert snapshot.is_anonymous
    assert snapshot.shows_tallies


def test_poll_creator_and_staff_may_close_but_a_bystander_may_not() -> None:
    snapshot = poll_snapshot(author_account_id=7, guild_id=10)

    assert snapshot.can_close(VoteActor(7, 70, guild_id=10)) is None
    assert snapshot.can_close(VoteActor(8, 80, guild_id=10, capabilities=CLOSE_ANY)) is None
    assert snapshot.can_close(VoteActor(8, 80, guild_id=10)) is VoteRejection.NOT_AUTHORIZED
    assert snapshot.can_close(VoteActor(7, 70, guild_id=999)) is VoteRejection.WRONG_GUILD


@pytest.mark.parametrize("kind", [VoteKind.BUILD, VoteKind.DELETE_LOG])
def test_threshold_kinds_are_not_closable_by_the_poll_command(kind: VoteKind) -> None:
    snapshot = build_snapshot(kind=kind, target=None)

    assert snapshot.can_close(VoteActor(7, 70, guild_id=10)) is VoteRejection.NOT_AUTHORIZED


@pytest.mark.parametrize("kind", [VoteKind.BUILD, VoteKind.DELETE_LOG])
def test_threshold_kinds_require_signed_thresholds(kind: VoteKind) -> None:
    validate_thresholds(kind, 3, -3)
    with pytest.raises(InvalidVoteConfigurationError, match="require both thresholds"):
        validate_thresholds(kind, None, None)
    with pytest.raises(InvalidVoteConfigurationError, match="positive and fail thresholds negative"):
        validate_thresholds(kind, 3, 3)
    with pytest.raises(InvalidVoteConfigurationError, match="positive and fail thresholds negative"):
        validate_thresholds(kind, -3, -3)


def test_generic_polls_reject_thresholds_entirely() -> None:
    validate_thresholds(VoteKind.GENERIC, None, None)
    with pytest.raises(InvalidVoteConfigurationError, match="must not carry vote thresholds"):
        validate_thresholds(VoteKind.GENERIC, 32767, -32768)


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
        RoleWeight(10, VoteKind.BUILD, 20, multiplier)


@pytest.mark.parametrize(("value", "seconds"), [("1m", 60), ("24h", 86400), ("30d", 2592000)])
def test_poll_duration_parser(value: str, seconds: int) -> None:
    assert parse_poll_duration(value) == seconds


@pytest.mark.parametrize("value", ["59s", "0m", "31d", "forever"])
def test_poll_duration_parser_rejects_out_of_range_values(value: str) -> None:
    with pytest.raises(InvalidVoteConfigurationError):
        parse_poll_duration(value)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(3600, "1 hour"), (86400, "24 hours"), (7 * 86400, "7 days"), (1800, "30 minutes"), (2 * 86400, "2 days")],
)
def test_duration_labels_match_the_presets_where_one_exists(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_option_lines_fall_back_to_the_guild_palette_for_missing_emojis() -> None:
    palette = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="1", guild_id=10, label="Option 1"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="2", guild_id=10, label="Option 2"),
    )

    options = parse_option_lines(["Red", "🔵 | Blue"], guild_id=10, palette=palette)

    assert [(option.emoji, option.label, option.id) for option in options] == [
        ("1️⃣", "Red", "1"),
        ("🔵", "Blue", "2"),
    ]


def test_option_lines_reject_duplicates_bad_counts_and_unusable_emojis() -> None:
    palette = (VoteOption("1️⃣", VoteChoice.GENERIC, identifier="1", guild_id=10, label="Option 1"),)

    with pytest.raises(InvalidVoteConfigurationError, match="between 2 and 10"):
        parse_option_lines(["Only one"], guild_id=10, palette=palette)
    with pytest.raises(InvalidVoteConfigurationError, match="unique"):
        parse_option_lines(["🔵 | Blue", "🔵 | Also blue"], guild_id=10, palette=palette)
    with pytest.raises(InvalidVoteConfigurationError, match="not accessible"):
        parse_option_lines(
            ["🔵 | Blue", "🟢 | Green"],
            guild_id=10,
            palette=palette,
            emoji_is_usable=lambda emoji: emoji != "🟢",
        )
    with pytest.raises(InvalidVoteConfigurationError, match="palette does not have enough"):
        parse_option_lines(["Red", "Blue"], guild_id=10, palette=palette)


def test_a_guild_poll_may_only_be_closed_from_the_guild_that_owns_it() -> None:
    poll = poll_snapshot(author_account_id=7, guild_id=10, scope=PollScope.GUILD)

    assert poll.can_close(VoteActor(7, 70, guild_id=10)) is None
    assert poll.can_close(VoteActor(7, 70, guild_id=999)) is VoteRejection.WRONG_GUILD


def test_a_network_poll_follows_its_author_but_pins_everyone_else() -> None:
    poll = poll_snapshot(author_account_id=7, guild_id=10, scope=PollScope.NETWORK)
    close_any = frozenset({VOTE_POLL_CLOSE_ANY.name})

    # The author may be standing in any guild the poll reached.
    assert poll.can_close(VoteActor(7, 70, guild_id=999)) is None
    # Staff elsewhere hold a capability resolved in the wrong guild.
    assert poll.can_close(VoteActor(8, 80, guild_id=999, capabilities=close_any)) is VoteRejection.WRONG_GUILD
    assert poll.can_close(VoteActor(8, 80, guild_id=10, capabilities=close_any)) is None
    assert poll.can_close(VoteActor(8, 80, guild_id=10)) is VoteRejection.NOT_AUTHORIZED
