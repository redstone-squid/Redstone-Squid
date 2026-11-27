"""Rebuilding ballots from the reactions on a session's cards."""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import override

import pytest

from squid.core.actors import ActingContext, Actor, WorkerActor
from squid.core.voting.application import VoteService
from squid.core.voting.application.recount import BallotRecounter, CardReactions
from squid.core.voting.domain import (
    CastVoteResult,
    VoteMessage,
    Voter,
    VoteRejection,
    VoteSelection,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)
from tests.support.voting import poll_snapshot

CARD = VoteMessage(100, 200, 10)


@dataclass
class Votes(VoteService):
    """Record casts; the stored snapshot does not change, as each account is judged once."""

    snapshot: VoteSessionSnapshot
    rejection: VoteRejection | None = None
    casts: list[tuple[Voter, str, str | None, bool]] = field(default_factory=list)

    @override
    async def get_session(self, vote_session_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        return self.snapshot if vote_session_id == self.snapshot.id else None

    @override
    async def _record_observed_reaction(
        self,
        voter: Actor,
        vote_session_id: int,
        option_id: str,
        *,
        emoji: str,
        count_revision: int | None = None,
        context: ActingContext,
    ) -> CastVoteResult:
        assert context.effective == WorkerActor(worker_name="recount")
        self.casts.append((Voter.of(voter.require_user()), option_id, emoji, False))
        return CastVoteResult(self.snapshot, self.rejection)


@dataclass
class Cards:
    reactions: dict[int, CardReactions]
    removed: list[tuple[str, int]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    async def read(self, card: VoteMessage, emojis: Sequence[str]) -> CardReactions | None:
        return self.reactions.get(card.id)

    async def remove(self, card: VoteMessage, emoji: str, user_id: int) -> None:
        self.removed.append((emoji, user_id))

    async def add_own(self, card: VoteMessage, emoji: str) -> None:
        self.added.append(emoji)


class Voters:
    """Discord user N votes as account N + 1000; user 99 has no consented account."""

    async def consented_account_id(self, discord_id: int) -> int | None:
        return None if discord_id == 99 else discord_id + 1000


def _snapshot(*, anonymous: bool = False, selections: tuple[VoteSelection, ...] = ()) -> VoteSessionSnapshot:
    visibility = VoteVisibility.ANONYMOUS_LIVE if anonymous else VoteVisibility.VISIBLE_LIVE
    return poll_snapshot(visibility=visibility, messages=(CARD,), selections=selections)


def _recounter(votes: Votes, cards: Cards) -> BallotRecounter:
    return BallotRecounter(votes, cards, Voters())


async def test_a_reaction_missed_while_offline_becomes_a_ballot() -> None:
    votes = Votes(_snapshot())
    cards = Cards({100: CardReactions({"2️⃣": [1]}, own=frozenset({"1️⃣", "2️⃣"}))})

    assert await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))

    # Select, not toggle, so repeating the recount cannot withdraw the ballot.
    assert votes.casts == [(Voter.account(1001), "two", "2️⃣", False)]
    assert (cards.removed, cards.added) == ([], [])


async def test_a_reaction_for_the_stored_option_wins_and_the_others_are_removed() -> None:
    stored = VoteSelection(Voter.account(1001), 10, "two", "2️⃣", 1)
    votes = Votes(_snapshot(selections=(stored,)))
    cards = Cards({100: CardReactions({"1️⃣": [1], "2️⃣": [1]}, own=frozenset({"1️⃣", "2️⃣"}))})

    assert not await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))

    assert votes.casts == []
    assert cards.removed == [("1️⃣", 1)]


async def test_conflicting_choices_without_a_ballot_remain_for_the_voter_to_resolve() -> None:
    votes = Votes(_snapshot())
    cards = Cards({100: CardReactions({"1️⃣": [1], "2️⃣": [1]})})

    assert not await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))
    assert votes.casts == []
    assert cards.removed == []


async def test_anonymous_reactions_are_removed_after_casting_even_without_an_account() -> None:
    votes = Votes(_snapshot(anonymous=True))
    cards = Cards({100: CardReactions({"1️⃣": [1, 99]}, own=frozenset({"1️⃣", "2️⃣"}))})

    await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))

    assert votes.casts == [(Voter.account(1001), "one", "1️⃣", False), (Voter.discord(99), "one", "1️⃣", False)]
    assert sorted(cards.removed) == [("1️⃣", 1), ("1️⃣", 99)]


async def test_a_refused_ballot_keeps_only_the_reaction_for_the_stored_option() -> None:
    stored = VoteSelection(Voter.account(1001), 10, "one", "1️⃣", 1)
    votes = Votes(_snapshot(selections=(stored,)), rejection=VoteRejection.NOT_ELIGIBLE)
    cards = Cards({100: CardReactions({"2️⃣": [1, 2]}, own=frozenset({"1️⃣", "2️⃣"}))})

    assert not await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))

    assert [cast[:2] for cast in votes.casts] == [(Voter.account(1001), "two"), (Voter.account(1002), "two")]
    assert sorted(cards.removed) == [("2️⃣", 1), ("2️⃣", 2)]


async def test_unreadable_cards_are_skipped_and_missing_option_reactions_restored() -> None:
    second = VoteMessage(101, 201, 10)
    votes = Votes(replace(_snapshot(), messages=(CARD, second)))
    cards = Cards({101: CardReactions({}, own=frozenset({"2️⃣"}))})

    assert not await _recounter(votes, cards).recount(1, context=ActingContext.worker("recount"))

    assert cards.added == ["1️⃣"]


@pytest.mark.parametrize("status", [VoteStatus.CLOSED, None])
async def test_closed_or_unknown_sessions_are_left_alone(status: VoteStatus | None) -> None:
    snapshot = _snapshot() if status is None else replace(_snapshot(), status=status)
    votes = Votes(snapshot)
    cards = Cards({100: CardReactions({"1️⃣": [1]})})

    assert not await _recounter(votes, cards).recount(
        2 if status is None else 1, context=ActingContext.worker("recount")
    )

    assert (votes.casts, cards.removed, cards.added) == ([], [], [])
