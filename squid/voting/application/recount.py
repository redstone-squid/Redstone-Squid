"""Rebuild stored ballots from the reactions on a voting session's cards.

People vote by reacting to a session's cards (its Discord messages), and the bot stores each
reaction as a ballot as the event arrives. A reaction added while the bot was offline is never
delivered, so the stored ballots can fall behind the cards. A recount reads the reactions that
are actually on the cards and casts the ballots they imply.

Reading every reacting user is one Discord request per hundred users per option per card, so
recounts are not run on a timer. They run when someone asks for one and immediately before a
session closes, so the final result reflects the cards. See
`docs/plans/discord-state-reconciliation.md`.

The recount never withdraws a ballot. A missing reaction does not show that the voter changed
their mind: the card may be unreachable, the reactions may have been cleared, or an anonymous
poll may have removed the reaction on purpose after storing the ballot.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from squid.voting.application.ports import VoteActorResolver
from squid.voting.application.services import VoteService
from squid.voting.domain import VoteMessage, VoteSessionSnapshot


@dataclass(frozen=True, slots=True)
class CardReactions:
    """The vote reactions on one card, as Discord currently reports them."""

    voters: Mapping[str, Sequence[int]]
    """Discord IDs of the people reacting with each requested emoji, in Discord's order.

    Bots, including this one, are left out. An emoji nobody reacted with may be missing.
    """
    own: frozenset[str] = field(default_factory=frozenset)
    """The requested emojis this bot has reacted with, which voters click to vote."""


class VoteCardReactions(Protocol):
    """Read and change the reactions on vote cards through Discord.

    The bot implements this with its gateway client and the worker with Discord's REST API.
    Emojis are written the way a stored vote option writes them: a Unicode emoji, or
    `<:name:id>` for a custom one.
    """

    async def read(self, card: VoteMessage, emojis: Sequence[str]) -> CardReactions | None:
        """Return the card's reactions for these emojis, or None if the card cannot be read."""
        ...

    async def remove(self, card: VoteMessage, emoji: str, user_id: int) -> None:
        """Remove one person's reaction, ignoring a card or reaction that is already gone."""
        ...

    async def add_own(self, card: VoteMessage, emoji: str) -> None:
        """React with an option's emoji as the bot, ignoring a card that is gone or locked."""
        ...


class ConsentedVoterLookup(Protocol):
    """Find the account a reacting Discord user may vote with."""

    async def consented_account_id(self, discord_id: int) -> int | None:
        """Return the account ID if the account exists and its consent is current.

        A reaction gives no chance to ask for consent, so this never creates an account.
        """
        ...


@dataclass(frozen=True, slots=True)
class _ObservedReaction:
    card: VoteMessage
    user_id: int
    emoji: str
    option_id: str


@dataclass(frozen=True, slots=True)
class _Ballot:
    """What a recount did with one account's reactions."""

    option_id: str | None
    """The option the account's stored ballot now has, if any."""
    changed: bool = False
    unresolved: bool = False
    """True when the ballot could not be judged, so its reactions must be left in place."""


class BallotRecounter:
    """Cast the ballots implied by the reactions on an open session's cards.

    Callers in the same process must not run a recount and a live reaction for the same
    session at once: both read the stored ballots and then change them. The bot serializes
    them with one lock per session. The worker only recounts a session right before it
    closes it, and casts use select semantics (`toggle=False`), so a live reaction that lands
    in the bot meanwhile cannot be withdrawn by the recount.
    """

    def __init__(
        self,
        votes: VoteService,
        cards: VoteCardReactions,
        voters: ConsentedVoterLookup,
        actors: VoteActorResolver,
    ) -> None:
        self._votes = votes
        self._cards = cards
        self._voters = voters
        self._actors = actors

    async def recount(self, vote_session_id: int) -> bool:
        """Make the session's ballots and card reactions agree, as far as Discord allows.

        For each account seen reacting:

        - If one of its reactions is for its stored option, the ballot stays.
        - Otherwise its first reaction, in card and option order, becomes its ballot.
        - Reactions for any other option are removed from public polls, and every reaction
          is removed from anonymous polls, whose cards must not show who voted.

        Reactions from people without a consented account are removed without casting.
        Accounts with no reactions keep their stored ballots. Finally the bot adds back any
        option reaction of its own that is missing, so people can still vote.

        Returns:
            Whether any ballot changed. Closed and unknown sessions are left alone.
        """
        snapshot = await self._votes.get_session(vote_session_id)
        if snapshot is None or not snapshot.is_open:
            return False

        # Read every reachable card.
        cards: list[tuple[VoteMessage, tuple[str, ...], CardReactions]] = []
        observed: dict[int, list[_ObservedReaction]] = {}
        for card in snapshot.messages:
            options = snapshot.options_for_guild(card.guild_id)
            emojis = tuple(option.emoji for option in options)
            reactions = await self._cards.read(card, emojis)
            if reactions is None:
                continue
            cards.append((card, emojis, reactions))
            for option in options:
                for user_id in reactions.voters.get(option.emoji, ()):
                    account_id = await self._voters.consented_account_id(user_id)
                    if account_id is None:
                        await self._cards.remove(card, option.emoji, user_id)
                        continue
                    observed.setdefault(account_id, []).append(
                        _ObservedReaction(card, user_id, option.emoji, option.id)
                    )

        # Cast one ballot per account and tidy its reactions.
        changed = False
        for account_id, reactions_for_account in observed.items():
            ballot = await self._apply(snapshot, account_id, reactions_for_account)
            changed = changed or ballot.changed
            if ballot.unresolved:
                continue
            for reaction in reactions_for_account:
                if snapshot.is_anonymous or reaction.option_id != ballot.option_id:
                    await self._cards.remove(reaction.card, reaction.emoji, reaction.user_id)

        # Put back the option reactions voters click.
        for card, emojis, reactions in cards:
            for emoji in emojis:
                if emoji not in reactions.own:
                    await self._cards.add_own(card, emoji)
        return changed

    async def _apply(
        self, snapshot: VoteSessionSnapshot, account_id: int, reactions: Sequence[_ObservedReaction]
    ) -> _Ballot:
        """Cast the ballot these reactions imply, unless the stored one already matches."""
        selection = snapshot.selection_for(account_id)
        stored = None if selection is None else selection.option_id
        if any(reaction.option_id == stored for reaction in reactions):
            return _Ballot(stored)
        desired = reactions[0]
        actor = await self._actors.resolve(account_id, desired.card.guild_id, snapshot.kind)
        if actor is None:
            # Discord could not confirm the voter's membership, so eligibility is unknown.
            # Keep the reactions: in an anonymous poll they are the only record of the vote.
            return _Ballot(stored, unresolved=True)
        result = await self._votes.cast_vote(snapshot.id, actor, desired.option_id, emoji=desired.emoji, toggle=False)
        if result.accepted:
            return _Ballot(desired.option_id, changed=True)
        return _Ballot(stored)
