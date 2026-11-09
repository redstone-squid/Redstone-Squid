"""Read and change the reactions on vote cards through a discord.py client."""

import contextlib
from collections.abc import Sequence

import discord

from squid.voting.application.recount import CardReactions
from squid.voting.domain import VoteMessage


class DiscordVoteCards:
    """Implement `VoteCardReactions` with any discord.py client.

    The bot passes its gateway client and the worker a gateway-less one from `rest_client`.
    Each read fetches the card, because a cached copy's reaction counts may be stale, and
    pages through the users of each option's reaction. A read therefore costs Discord
    requests in proportion to the number of voters.

    A card that is deleted or hidden reads as None, and removing or adding a reaction on
    it does nothing. Other Discord failures, such as a rate limit longer than the client
    accepts or an outage, propagate.
    """

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    async def read(self, card: VoteMessage, emojis: Sequence[str]) -> CardReactions | None:
        try:
            message = await self._message(card).fetch()
        except discord.NotFound, discord.Forbidden:
            return None
        reactions = {str(reaction.emoji): reaction for reaction in message.reactions}
        voters: dict[str, list[int]] = {}
        for emoji in emojis:
            reaction = reactions.get(emoji)
            if reaction is not None:
                voters[emoji] = [user.id async for user in reaction.users(limit=None) if not user.bot]
        own = frozenset(emoji for emoji in emojis if (reaction := reactions.get(emoji)) is not None and reaction.me)
        return CardReactions(voters, own)

    async def remove(self, card: VoteMessage, emoji: str, user_id: int) -> None:
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await self._message(card).remove_reaction(emoji, discord.Object(user_id))

    async def add_own(self, card: VoteMessage, emoji: str) -> None:
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await self._message(card).add_reaction(emoji)

    def _message(self, card: VoteMessage) -> discord.PartialMessage:
        return self._client.get_partial_messageable(card.channel_id).get_partial_message(card.id)
