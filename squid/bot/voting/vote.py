"""Handles reaction-based voting for various purposes."""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.utils.components import no_mentions, text_layout
from squid.bot.utils.permissions import is_staff, is_trusted_or_staff
from squid.bot.voting.base_session import AbstractVoteSession
from squid.bot.voting.build_session import BuildVoteSession
from squid.bot.voting.delete_log_session import DeleteLogVoteSession
from squid.voting.domain import VoteActor

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.vote_service = bot.services.votes
        self.builds = bot.services.builds
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def get_vote_session(
        self, message_id: int, *, status: Literal["open", "closed"] | None = None
    ) -> AbstractVoteSession | None:
        """Gets a vote session from the database.

        Args:
            message_id: The message ID of the vote session.
            status: The status of the vote session. If None, it will get any status.
        """
        snapshot = await self.vote_service.get_session(message_id)
        if snapshot is None or (status is not None and snapshot.status != status):
            return None
        if snapshot.kind == "build":
            return await BuildVoteSession.from_id(self.bot, snapshot.id)
        if snapshot.kind == "delete_log":
            return await DeleteLogVoteSession.from_id(self.bot, snapshot.id)
        logger.error("Unknown vote session kind: %s", snapshot.kind)
        msg = f"Unknown vote session kind: {snapshot.kind}"
        raise NotImplementedError(msg)

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await self.get_vote_session(payload.message_id, status="open")
        if vote_session is None:
            return

        # Remove the user's reaction to keep votes anonymous
        channel = cast(GuildMessageable, self.bot.get_channel(payload.channel_id))
        message = await channel.fetch_message(payload.message_id)
        user = self.bot.get_user(payload.user_id)
        assert user is not None
        remove_reaction_task = asyncio.create_task(message.remove_reaction(payload.emoji, user))
        self._background_tasks.add(remove_reaction_task)
        remove_reaction_task.add_done_callback(self._background_tasks.discard)

        if user.bot:
            return  # Ignore bot reactions

        emoji_name = str(payload.emoji)
        if emoji_name not in {option.emoji for option in vote_session.options}:
            return
        user_id = payload.user_id

        staff = await is_staff(self.bot, payload.guild_id, user_id)
        trusted = False
        if isinstance(vote_session, DeleteLogVoteSession):
            if payload.guild_id is None:
                # Voting in DMs is not implemented
                return
            trusted = await is_trusted_or_staff(self.bot, payload.guild_id, user_id)

        result = await self.vote_service.cast_vote(
            payload.message_id,
            VoteActor(user_id=user_id, is_staff=staff, is_trusted=trusted),
            emoji_name,
        )
        if result.rejection == "not_eligible":
            await channel.send(
                view=text_layout("You do not have a trusted role."),
                allowed_mentions=no_mentions(),
            )
            return
        if not result.accepted or result.session is None:
            return

        vote_session.apply_persisted_state(result.session)
        if result.just_closed:
            if isinstance(vote_session, BuildVoteSession):
                build_id = result.session.target.build_id
                assert build_id is not None
                if result.session.result == "approved":
                    vote_session.build = await self.builds.confirm(build_id)
                else:
                    vote_session.build = await self.builds.deny(build_id)
            elif isinstance(vote_session, DeleteLogVoteSession) and result.session.result == "approved":
                with contextlib.suppress(discord.NotFound):
                    await vote_session.target_message.delete()
        await vote_session.update_messages()

    @hybrid_group(name="vote")
    async def vote_group(self, ctx: Context[BotT]) -> None:
        """Start and manage votes."""
        await ctx.send_help("vote")

    @vote_group.command(name="delete")
    @app_commands.rename(target_message="message")
    @app_commands.describe(target_message="The message to hold a deletion vote for.")
    async def start_vote(self, ctx: Context[BotT], target_message: discord.Message):
        """Start a vote to delete a message."""
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send(
                view=text_layout("The message is not from this guild."),
                allowed_mentions=no_mentions(),
            )
            return

        async with self.bot.get_running_message(ctx) as message:
            await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
