"""Handles the reaction-based voting system for submissions."""

import discord
from discord.ext.commands import Bot, Cog

import database.message as msg
from database.enums import Status
from database.message import get_build_id_by_message
from database.server_settings import get_server_setting
from database.builds import Build
from bot.config import OWNER_ID
from bot.schema import GuildMessageable
from bot.submission import post


class VotingCog(Cog, name="vote", command_attrs=dict(hidden=True)):
    def __init__(self, bot: Bot):
        self.bot = bot

    @Cog.listener(name="on_raw_reaction_add")
    async def confirm_record(self, payload: discord.RawReactionActionEvent):
        """Listens for reactions on the vote channel and confirms the submission if the reaction is a thumbs up."""
        # --- A bunch of checks to make sure the reaction is valid ---
        # Must be in a guild
        if (guild_id := payload.guild_id) is None:
            return

        # Must be in the vote channel
        vote_channel_id = await get_server_setting(guild_id, "Vote")
        if vote_channel_id is None or payload.channel_id != vote_channel_id:
            return

        # Must be users that are allowed to vote
        if payload.user_id != OWNER_ID:
            return

        # The message must be from the bot
        message: discord.Message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)  # type: ignore[attr-defined]
        if message.author.id != self.bot.user.id:  # type: ignore[attr-defined]
            return

        # A build ID must be associated with the message
        build_id = await get_build_id_by_message(payload.message_id)
        if build_id is None:
            return

        # The submission status must be pending
        submission = await Build.from_id(build_id)
        assert submission is not None
        if submission.submission_status != Status.PENDING:
            return
        # --- End of checks ---

        # If the reaction is a thumbs up, confirm the submission
        if payload.emoji.name == "👍":
            # TODO: Count the number of thumbs up reactions and confirm if it passes a threshold
            await submission.confirm()
            message_ids = await msg.delete_message(guild_id, build_id)
            await post.post_build(self.bot, submission)
            for message_id in message_ids:
                vote_channel = self.bot.get_channel(vote_channel_id)
                if isinstance(vote_channel, GuildMessageable):
                    message = await vote_channel.fetch_message(message_id)
                    await message.delete()
                else:
                    # TODO: Add a check when adding vote channels to the database
                    raise ValueError(f"Invalid channel type for a vote channel: {type(vote_channel)}")


async def setup(bot: Bot):
    await bot.add_cog(VotingCog(bot))
