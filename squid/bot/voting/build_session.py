"""Discord vote sessions for build changes."""

import asyncio
import contextlib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Literal, final, override

import discord

from squid.bot.message_adapter import to_tracked_message
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
        if self.type == "add":
            changes: list[VoteChange] = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        else:
            original = await self.bot.services.builds.get(self.build.id)
            assert original is not None
            changes = original.diff(self.build)

        self.id = await self.bot.services.votes.start_build_vote(
            author_id=self.author_id,
            pass_threshold=self.pass_threshold,
            fail_threshold=self.fail_threshold,
            build_id=self.build.id,
            changes=changes,
            options=self.options,
        )
        await track_vote_messages(
            await self.fetch_messages(),
            self.bot.services.messages,
            self.id,
            build_id=self.build.id,
        )

        await self.update_messages()

        reaction_tasks = [
            message.add_reaction(self.primary_emoji(choice))
            for message in self._messages
            for choice in (VoteChoice.APPROVE, VoteChoice.DENY)
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
            author_id=snapshot.author_id,
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
            content=self.build.original_link, embed=await self.bot.for_build(self.build).generate_embed()
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
        embed = await self.bot.for_build(self.build).generate_embed()
        embed.add_field(name="", value="", inline=False)  # Add a blank field to separate the vote count
        embed.add_field(name="Accept", value=f"{self.upvotes}/{self.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{self.downvotes}/{-self.fail_threshold}", inline=True)
        await asyncio.gather(
            *[message.edit(content=self.build.original_link, embed=embed) for message in await self.fetch_messages()]
        )

    @classmethod
    async def get_open_vote_sessions(
        cls: type["BuildVoteSession"], bot: "squid.bot.app.RedstoneSquid"
    ) -> "list[BuildVoteSession]":
        """Get all open vote sessions from the database."""
        sessions = await asyncio.gather(
            *(cls._from_snapshot(bot, snapshot) for snapshot in await bot.services.votes.list_open(cls.kind))
        )
        return [session for session in sessions if session is not None]
