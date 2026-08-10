"""Discord vote sessions for deleting log messages."""

import asyncio
import contextlib
from collections.abc import Iterable, Sequence
from textwrap import dedent
from typing import TYPE_CHECKING, final, override

import discord

from squid.bot.utils.components import (
    DISCORD_GREEN,
    DISCORD_RED,
    DISCORD_YELLOW,
    CardField,
    StaticLayout,
    card_layout,
    edit_layout,
    no_mentions,
)
from squid.bot.voting.base_session import AbstractVoteSession
from squid.bot.voting.message_tracking import track_vote_messages
from squid.voting.domain import DEFAULT_VOTE_OPTIONS, VoteChoice, VoteOption, VoteSessionSnapshot

if TYPE_CHECKING:
    import squid.bot.app


@final
class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message from the log."""

    kind = "delete_log"

    def __init__(
        self,
        bot: "squid.bot.app.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ):
        """
        Initialize the delete log vote session.

        Args:
            bot: The discord client.
            messages: The messages (or their ids) belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            target_message: The message to delete if the vote passes.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold, options)
        self.target_message = target_message

    @classmethod
    @override
    async def create(
        cls,
        bot: "squid.bot.app.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> "DeleteLogVoteSession":
        self = await super().create(bot, messages, author_id, target_message, pass_threshold, fail_threshold, options)
        assert isinstance(self, DeleteLogVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        assert self.target_message.guild is not None
        self.options = (await self.bot.services.votes.emoji_preset(self.target_message.guild.id, self.kind)).options
        author = await self.bot.services.accounts.get_or_create_account(self.author_id)
        assert author.id is not None
        self.id = await self.bot.services.votes.start_delete_log_vote(
            author_account_id=author.id,
            pass_threshold=self.pass_threshold,
            fail_threshold=self.fail_threshold,
            message_id=self.target_message.id,
            channel_id=self.target_message.channel.id,
            server_id=self.target_message.guild.id,
            options=self.options,
        )
        await track_vote_messages(
            await self.fetch_messages(),
            self.bot.services.messages,
            self.id,
        )
        await self.update_messages()
        reaction_tasks = [message.add_reaction(option.emoji) for message in self._messages for option in self.options]
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*reaction_tasks)  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: "squid.bot.app.RedstoneSquid", vote_session_id: int) -> "DeleteLogVoteSession | None":
        snapshot = await bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None or snapshot.kind != cls.kind:
            return None
        return await cls._from_snapshot(bot, snapshot)

    @classmethod
    async def _from_snapshot(
        cls, bot: "squid.bot.app.RedstoneSquid", snapshot: VoteSessionSnapshot
    ) -> "DeleteLogVoteSession | None":
        """Restore a Discord view from an application snapshot."""
        target = snapshot.target
        if target.channel_id is None or target.message_id is None:
            return None
        target_message = await bot.get_or_fetch_message(target.channel_id, target.message_id)
        if target_message is None:
            return None

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot,
            snapshot.message_ids,
            0,
            target_message,
            snapshot.pass_threshold,
            snapshot.fail_threshold,
            snapshot.options,
        )
        self.id = snapshot.id
        self._message_channels = {message.id: message.channel_id for message in snapshot.messages}
        self.apply_persisted_state(snapshot)
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        layout = self._render_layout(
            title="Vote to Delete Log",
            action=(
                f"React with {self.primary_emoji(VoteChoice.APPROVE)} to approve or "
                f"{self.primary_emoji(VoteChoice.DENY)} to deny."
            ),
            accent_colour=DISCORD_YELLOW,
        )
        return await channel.send(view=layout, allowed_mentions=no_mentions())

    @override
    async def update_messages(self) -> None:
        """Updates the message with the current vote count."""
        match self.result:
            case "pending":
                title = "Vote to Delete Log"
                action = (
                    f"React with {self.primary_emoji(VoteChoice.APPROVE)} to upvote or "
                    f"{self.primary_emoji(VoteChoice.DENY)} to downvote."
                )
                accent_colour = DISCORD_YELLOW
            case "approved":
                title = "Vote to Delete Log: Passed"
                action = ""
                accent_colour = DISCORD_GREEN
            case "denied":
                title = "Vote to Delete Log: Failed"
                action = ""
                accent_colour = DISCORD_RED
            case _:
                title = "Vote to Delete Log: Closed"
                action = ""
                accent_colour = DISCORD_YELLOW

        layout = self._render_layout(title=title, action=action, accent_colour=accent_colour)
        await asyncio.gather(
            *(edit_layout(message, layout, allowed_mentions=no_mentions()) for message in await self.fetch_messages())
        )

    def _render_layout(self, *, title: str, action: str, accent_colour: int) -> StaticLayout:
        description = dedent(f"""
            {action}

            **Log content**
            {self.target_message.content}
            """).strip()
        return card_layout(
            title,
            description,
            accent_colour=accent_colour,
            fields=(
                CardField("Upvotes", str(self.upvotes)),
                CardField("Downvotes", str(self.downvotes)),
                CardField("Net votes", str(self.net_votes)),
            ),
        )

    @classmethod
    async def get_open_vote_sessions(
        cls: "type[DeleteLogVoteSession]", bot: "squid.bot.app.RedstoneSquid"
    ) -> "list[DeleteLogVoteSession]":
        """Get all open vote sessions from the database."""
        sessions = await asyncio.gather(
            *(cls._from_snapshot(bot, snapshot) for snapshot in await bot.services.votes.list_open(cls.kind))
        )
        return [session for session in sessions if session is not None]
