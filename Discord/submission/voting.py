"""Handles the reaction-based voting system for submissions."""
import discord
from discord.ext.commands import Bot, Cog

import Database.message as msg
from Database.message import get_submission_id_by_message
from Database.server_settings import get_server_setting
from Database.submissions import get_submission, confirm_submission
from Discord.config import OWNER_ID
from Discord.submission import post
from Discord.submission.submission import Submission


class VotingCog(Cog, name="vote", command_attrs=dict(hidden=True)):
    def __init__(self, bot: Bot):
        self.bot = bot

    @Cog.listener(name='on_raw_reaction_add')
    async def confirm_record(self, payload: discord.RawReactionActionEvent):
        """Listens for reactions on the vote channel and confirms the submission if the reaction is a thumbs up."""
        vote_channel_id = get_server_setting(payload.guild_id, 'Vote')
        # Must be in the vote channel
        if vote_channel_id is None or payload.channel_id != vote_channel_id:
            return

        # Must be users that are allowed to vote
        if payload.user_id != OWNER_ID:
            return

        # The message must be from the bot
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        if message.author.id != self.bot.user.id:
            return

        # The submission status must be pending
        submission_id = get_submission_id_by_message(payload.message_id)
        # No submission found (message is not a submission)
        if submission_id is None:
            return
        submission = get_submission(submission_id)
        if submission.submission_status != Submission.PENDING:
            return

        # If the reaction is a thumbs up, confirm the submission
        if payload.emoji.name == '👍':
            # TODO: Count the number of thumbs up reactions and confirm if it passes a threshold
            confirm_submission(submission_id)
            message_ids = msg.delete_message(payload.guild_id, submission_id)
            for message_id in message_ids:
                message = await self.bot.get_channel(vote_channel_id).fetch_message(message_id)
                await message.delete()
            # Refreshes the submission from the database
            submission = get_submission(submission_id)
            await post.send_submission(self.bot, submission)
