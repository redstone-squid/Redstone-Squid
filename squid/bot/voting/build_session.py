"""Discord vote sessions for build changes."""

import asyncio
import contextlib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Literal, final, override

import discord

from squid.bot.message_adapter import to_tracked_message
from squid.bot.utils.components import StaticLayout, edit_layout, no_mentions
from squid.bot.voting.base_session import AbstractVoteSession
from squid.bot.voting.message_tracking import track_vote_messages
from squid.builds.domain import Build, Status
from squid.voting.domain import DEFAULT_VOTE_OPTIONS, VoteChange, VoteChoice, VoteOption, VoteSessionSnapshot

if TYPE_CHECKING:
    import squid.bot.app


@final
class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    kind = "build"

    def __init__(
        self,
        bot: "squid.bot.app.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ):
        """
        Initialize the vote session.

        Args:
            bot: The discord bot.
            messages: The messages belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            build: The build which the vote session is for. If type is "update", this is the updated build.
            type: Whether to add or update the build.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold, options)
        self.build = build
        self.type = type

    @classmethod
    @override
    async def create(
        cls,
        bot: "squid.bot.app.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> "BuildVoteSession":
        self = await super().create(bot, messages, author_id, build, type, pass_threshold, fail_threshold, options)
        assert isinstance(self, BuildVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        assert self.build.id is not None
        messages = await self.fetch_messages()
        guild_ids = {message.guild.id for message in messages if message.guild is not None}
        resolved_options: list[VoteOption] = []
        for guild_id in guild_ids:
            resolved_options.extend((await self.bot.services.votes.emoji_preset(guild_id, self.kind)).options)
        self.options = tuple(resolved_options)
        if self.type == "add":
            changes: list[VoteChange] = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        else:
            original = await self.bot.services.builds.get(self.build.id)
            assert original is not None
            changes = original.diff(self.build)

        author = await self.bot.services.accounts.get_or_create_account(self.author_id)
        assert author.id is not None
        self.id = await self.bot.services.votes.start_build_vote(
            author_account_id=author.id,
            pass_threshold=self.pass_threshold,
            fail_threshold=self.fail_threshold,
            build_id=self.build.id,
            changes=changes,
            options=self.options,
        )
        await track_vote_messages(
            messages,
            self.bot.services.messages,
            self.id,
            build_id=self.build.id,
        )

        await self.update_messages()

        reaction_tasks = [
            message.add_reaction(option.emoji)
            for message in self._messages
            if message.guild is not None
            for option in self.options
            if option.guild_id == message.guild.id
        ]
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*reaction_tasks)  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: "squid.bot.app.RedstoneSquid", vote_session_id: int) -> "BuildVoteSession | None":
        snapshot = await bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None or snapshot.kind != cls.kind:
            return None
        return await cls._from_snapshot(bot, snapshot)

    @classmethod
    async def _from_snapshot(
        cls, bot: "squid.bot.app.RedstoneSquid", snapshot: VoteSessionSnapshot
    ) -> "BuildVoteSession | None":
        """Restore a Discord view from an application snapshot."""
        if snapshot.target.build_id is None:
            msg = f"Found a build vote session with no associated build id. session_id={snapshot.id}"
            raise ValueError(msg)

        build = await bot.services.builds.get(snapshot.target.build_id)
        if build is None:
            return None
        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot=bot,
            messages=snapshot.message_ids,
            author_id=0,
            build=build,
            type="add",  # TODO: Handle update type properly
            pass_threshold=snapshot.pass_threshold,
            fail_threshold=snapshot.fail_threshold,
            options=snapshot.options,
        )
        self.id = snapshot.id
        self._message_channels = {message.id: message.channel_id for message in snapshot.messages}
        self.apply_persisted_state(snapshot)
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        message = await channel.send(
            view=await self.bot.for_build(self.build).render_layout(),
            allowed_mentions=no_mentions(),
        )
        await self.bot.services.messages.track(
            to_tracked_message(message),
            purpose="vote",
            build_id=self.build.id,
            vote_session_id=self.id,
        )
        self._messages.add(message)
        return message

    @override
    async def update_messages(self):
        async def update(message: discord.Message) -> None:
            container = await self.bot.for_build(self.build).render_container()
            container.add_item(discord.ui.Separator())
            if self.is_closed:
                result_label = {
                    "approved": "Approved",
                    "denied": "Denied",
                    "cancelled": "Closed without a decision",
                }[self.result]
                vote_text = f"### Vote closed — {result_label}\n**Final score:** {self.net_votes:g}"
            else:
                guild_id = message.guild.id if message.guild is not None else None
                approve_emoji = self.primary_emoji(VoteChoice.APPROVE, guild_id)
                deny_emoji = self.primary_emoji(VoteChoice.DENY, guild_id)
                vote_text = (
                    "### Vote in progress\n"
                    f"React with {approve_emoji} to **accept** or {deny_emoji} to **deny**. Votes are anonymous.\n"
                    f"**Accept:** {self.upvotes:g}/{self.pass_threshold}  •  "
                    f"**Deny:** {self.downvotes:g}/{-self.fail_threshold}"
                )
            container.add_item(discord.ui.TextDisplay(vote_text))
            await edit_layout(message, StaticLayout(container), allowed_mentions=no_mentions())

        await asyncio.gather(*(update(message) for message in await self.fetch_messages()))

    @classmethod
    async def get_open_vote_sessions(
        cls: type["BuildVoteSession"], bot: "squid.bot.app.RedstoneSquid"
    ) -> "list[BuildVoteSession]":
        """Get all open vote sessions from the database."""
        sessions = await asyncio.gather(
            *(cls._from_snapshot(bot, snapshot) for snapshot in await bot.services.votes.list_open(cls.kind))
        )
        return [session for session in sessions if session is not None]
