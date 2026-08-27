"""The narrow poll facade the wizard views talk to.

The wizard used to hold the whole `VoteCog`, which gave a modal reach over reaction
dispatch and every service on the bot. It needs exactly three things: the guild's
emoji palette, option parsing, and "create this poll and put it in this channel".
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

import discord

from squid.bot._types import GuildMessageable
from squid.bot.ui import render_payload, text_node
from squid.bot.utils.permissions import build_subject
from squid.permissions.domain.catalogue import VOTE_POLL_NETWORK_CREATE
from squid.voting.domain import PollScope, VoteKind, VoteOption, VoteVisibility
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app


class PollPublisher(Protocol):
    """Poll creation and Discord publication, as the wizard views need it."""

    async def resolve_options(self, guild_id: int, lines: Sequence[str]) -> tuple[VoteOption, ...]: ...

    async def create_and_publish(
        self,
        *,
        author_account_id: int,
        channel: GuildMessageable,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
        scope: PollScope = PollScope.GUILD,
    ) -> discord.Message: ...

    async def may_create_network(self, member: discord.Member) -> bool: ...


class DiscordPollPublisher:
    """Publish polls into Discord channels on behalf of the wizard."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self._bot = bot

    async def palette(self, guild_id: int) -> tuple[VoteOption, ...]:
        """Return the guild's configured generic emoji palette."""
        return (await self._bot.services.votes.emoji_preset(guild_id, VoteKind.GENERIC)).options

    async def resolve_options(self, guild_id: int, lines: Sequence[str]) -> tuple[VoteOption, ...]:
        """Turn wizard option lines into stable options, filling aliases from the palette."""
        from squid.bot.voting.poll_wizard import parse_option_lines

        return parse_option_lines(
            lines,
            guild_id=guild_id,
            palette=await self.palette(guild_id),
            emoji_is_usable=lambda emoji: _emoji_is_usable(self._bot, guild_id, emoji),
        )

    async def create_and_publish(
        self,
        *,
        author_account_id: int,
        channel: GuildMessageable,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
        scope: PollScope = PollScope.GUILD,
    ) -> discord.Message:
        """Persist the poll, then hand one Discord message to the reconciler.

        Creation comes first and takes no channel, so a send that fails leaves an
        attachable poll rather than a half-made one. The card's location is a human
        decision -- the channel the command was run in -- so it is sent here and
        adopted, rather than the renderer inventing somewhere to put it.
        """
        if scope is PollScope.NETWORK:
            # Aliases resolved against the author's guild would leave every other
            # server's card with nothing to react to.
            options = [replace(option, guild_id=None) for option in options]
        session_id = await self._bot.services.votes.create_generic_poll(
            author_account_id=author_account_id,
            question=question,
            visibility=visibility,
            duration_seconds=duration_seconds,
            options=options,
            guild_id=channel.guild.id,
            scope=scope,
        )
        return await self.attach(session_id, channel)

    async def may_create_network(self, member: discord.Member) -> bool:
        """Whether `member` may publish a poll into every server's vote channel."""
        subject = await build_subject(self._bot, member, member.guild.id)
        capabilities = await self._bot.services.permissions.capabilities(subject, (VOTE_POLL_NETWORK_CREATE,))
        return VOTE_POLL_NETWORK_CREATE.name in capabilities

    async def attach(self, vote_session_id: int, channel: GuildMessageable) -> discord.Message:
        """Post one card for an existing poll and let the reconcile loop own it."""
        result = await send_to(channel)(render_payload([text_node("Publishing poll…")]))
        message = result.message
        if message is None:
            detail = "poll placeholder delivery returned no message"
            raise RuntimeError(detail)
        await self._bot.post_reconciler.adopt(message, "vote_session", str(vote_session_id), "vote_card")
        await self._bot.refresh_posts("vote_session", str(vote_session_id))
        return message


def _emoji_is_usable(bot: squid.bot.app.RedstoneSquid, guild_id: int, emoji: str) -> bool:
    """Whether the bot may react with `emoji` in `guild_id`."""
    parsed = discord.PartialEmoji.from_str(emoji)
    if not parsed.is_custom_emoji():
        return True
    guild = bot.get_guild(guild_id)
    custom = guild.get_emoji(parsed.id or 0) if guild is not None else None
    return custom is not None and custom.is_usable()
