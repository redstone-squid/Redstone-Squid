from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from whenever import Instant

from squid.reactions.domain import ReactionActor
from squid.starboard.domain import (
    EntryAction,
    OriginMessage,
    StarboardConfig,
    StarboardEmoji,
    StarboardEntry,
    decide_entry_action,
    evaluate_vote,
)

NOW = Instant.parse_iso("2026-08-04T12:00:00Z")


def config(**changes: object) -> StarboardConfig:
    return replace(
        StarboardConfig(1, 10, 20, "main", (StarboardEmoji("⭐", "up"), StarboardEmoji("💩", "down"))),
        **changes,
    )


def origin(**changes: object) -> OriginMessage:
    return replace(OriginMessage(100, 10, 30, 40, author_is_bot=False, posted_at=NOW.subtract(seconds=60)), **changes)


@pytest.mark.parametrize(
    ("changes", "actor", "expected"),
    [
        ({}, ReactionActor(40), "remove_reaction"),
        ({"self_vote": True}, ReactionActor(40), "accept"),
        ({"allow_bots": False}, ReactionActor(41), "accept"),
        ({"require_image": True}, ReactionActor(41), "remove_reaction"),
        ({"min_age_seconds": 61}, ReactionActor(41), "remove_reaction"),
        ({"max_age_seconds": 59}, ReactionActor(41), "remove_reaction"),
    ],
)
def test_vote_policy(changes: dict[str, object], actor: ReactionActor, expected: str) -> None:
    assert evaluate_vote(config(**changes), origin(), actor, "⭐", now=NOW).action == expected


def test_vote_policy_rejects_bot_authors_and_accepts_age_boundaries() -> None:
    assert (
        evaluate_vote(config(), origin(author_is_bot=True), ReactionActor(41), "⭐", now=NOW).action
        == "remove_reaction"
    )
    bounded = config(min_age_seconds=60, max_age_seconds=60)
    assert evaluate_vote(bounded, origin(), ReactionActor(41), "⭐", now=NOW).action == "accept"


def test_unconfigured_reactions_and_deleted_origins_are_ignored() -> None:
    assert evaluate_vote(config(), origin(), ReactionActor(41), "other", now=NOW).action == "ignore"
    assert evaluate_vote(config(), origin(deleted_at=NOW), ReactionActor(41), "⭐", now=NOW).action == "ignore"


@pytest.mark.parametrize(
    ("score", "posted", "expected"),
    [
        (-1, False, EntryAction.NOOP),
        (0, True, EntryAction.REMOVE),
        (0.1, False, EntryAction.NOOP),
        (0.1, True, EntryAction.UPDATE),
        (3, False, EntryAction.SEND),
        (3, True, EntryAction.UPDATE),
    ],
)
def test_entry_hysteresis(score: float, posted: bool, expected: EntryAction) -> None:
    entry = StarboardEntry(1, 100, posted_message_id=200 if posted else None)
    assert decide_entry_action(config(), entry, score, origin_present=True) is expected


def test_origin_delete_only_removes_linked_existing_posts() -> None:
    posted = StarboardEntry(1, 100, posted_message_id=200)
    assert decide_entry_action(config(), posted, 5, origin_present=False) is EntryAction.REMOVE
    assert decide_entry_action(config(link_deletes=False), posted, 5, origin_present=False) is EntryAction.NOOP
    assert decide_entry_action(config(), StarboardEntry(1, 100), 5, origin_present=False) is EntryAction.NOOP


@given(st.lists(st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False), min_size=1))
def test_increasing_scores_never_remove_after_send(scores: list[float]) -> None:
    has_sent = False
    entry = StarboardEntry(1, 100)
    for score in sorted(scores):
        action = decide_entry_action(config(), entry, score, origin_present=True)
        assert not (has_sent and action is EntryAction.REMOVE)
        if action is EntryAction.SEND:
            has_sent = True
            entry = replace(entry, posted_message_id=200)
