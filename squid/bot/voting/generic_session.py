"""Discord presentation for user-created generic polls."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, final

import discord

from squid.bot.utils.components import edit_layout, no_mentions, text_layout
from squid.voting.domain import VoteMessage, VoteSessionSnapshot

if TYPE_CHECKING:
    import squid.bot.app


@final
class GenericVoteSession:
    """Restore and render one generic poll from its application snapshot."""

    kind = "generic"

    def __init__(self, bot: "squid.bot.app.RedstoneSquid", snapshot: VoteSessionSnapshot):
        self.bot = bot
        self.snapshot = snapshot
        self.id = snapshot.id
        self.message_ids = set(snapshot.message_ids)

    @classmethod
    async def from_id(cls, bot: "squid.bot.app.RedstoneSquid", vote_session_id: int) -> "GenericVoteSession | None":
        snapshot = await bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None or snapshot.kind != "generic" or snapshot.poll is None:
            return None
        return cls(bot, snapshot)

    def apply_persisted_state(self, snapshot: VoteSessionSnapshot) -> None:
        self.snapshot = snapshot

    async def update_messages(self) -> None:
        await asyncio.gather(*(self._update(message) for message in self.snapshot.messages))

    async def _update(self, location: VoteMessage) -> None:
        message = await self.bot.get_or_fetch_message(location.channel_id, location.id)
        if message is not None:
            await edit_layout(message, text_layout(self.render()), allowed_mentions=no_mentions())

    def render(self) -> str:
        poll = self.snapshot.poll
        assert poll is not None
        closed = self.snapshot.status == "closed"
        show_totals = poll.visibility != "anonymous_hidden" or closed
        raw = self.snapshot.raw_tallies()
        weighted = self.snapshot.weighted_tallies()
        lines = [f"## {poll.question}"]
        for option in self.snapshot.options_for_guild(poll.guild_id):
            line = f"{option.emoji} **{option.label}**"
            if show_totals:
                line += f" — {raw.get(option.identifier or '', 0)} votes, {weighted.get(option.identifier or '', 0):g} weighted"
            if poll.visibility == "visible_live" and show_totals:
                voters = [
                    f"<@{vote.user_id}>" for vote in self.snapshot.selections if vote.option_id == option.identifier
                ]
                if voters:
                    line += f" ({', '.join(voters)})"
            lines.append(line)
        if closed:
            totals = self.snapshot.weighted_tallies()
            best = max(totals.values(), default=0)
            winners = [
                option.label or option.identifier or option.emoji
                for option in self.snapshot.options
                if totals.get(option.identifier or "", 0) == best
            ]
            outcome = (
                "No votes"
                if not totals
                else f"Tie: {', '.join(winners)}"
                if len(winners) > 1
                else f"Winner: {winners[0]}"
            )
            lines.append(f"\n**Poll closed — {outcome}**")
        else:
            lines.append(f"\nCloses <t:{poll.deadline.timestamp()}:R>.")
        return "\n".join(lines)

    async def add_reactions(self, message: discord.Message) -> None:
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*(message.add_reaction(option.emoji) for option in self.snapshot.options))
