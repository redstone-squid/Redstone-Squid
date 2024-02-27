import time
from typing import Literal, Optional

import discord
from discord import app_commands, InteractionResponse
from discord.ext import commands

import Database.main as DB

class MyGroup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name='submit')
    async def submit(self, interaction: discord.Interaction, record_category: Literal['Smallest', 'Fastest', 'First'],
                     door_width: int, door_height: int, pattern: str, door_type: str, width_of_build: int, height_of_build: int, depth_of_build: int,
                     works_in: str,
                     first_order_restrictions: str = '',
                     second_order_restrictions: str = '', information_about_build: str = '',
                     relative_closing_time: int = -1,
                     relative_opening_time: int = -1, date_of_creation: str = '', in_game_name_of_creator: str = '',
                     locationality: str = '', directionality: str = '',
                     link_to_image: str = '', link_to_youtube_video: str = '',
                     link_to_world_download: str = '', server_ip: str = '', coordinates: str = '',
                     command_to_get_to_build: str = '', your_ign_or_discord: str = ''):
        """Submits a record to the database directly."""
        # TODO: Discord only allows 25 options, so we have to split the options into two commands.
        # For now, ignore the absolute times.
        absolute_closing_time = ''
        absolute_opening_time = ''
        # formatted as 22/02/2024 12:21:48
        response: InteractionResponse = interaction.response
        await response.defer()
        followup: discord.Webhook = interaction.followup
        message: discord.WebhookMessage | None = await followup.send('Received')
        timestamp = time.strftime('%d/%m/%Y %H:%M:%S')
        form_wks = DB.get_form_submissions_worksheet()
        form_wks.append_row([record_category, door_width, door_height, pattern, door_type, first_order_restrictions,
                             second_order_restrictions,
                             information_about_build, width_of_build, height_of_build, depth_of_build,
                             relative_closing_time, relative_opening_time,
                             absolute_closing_time, absolute_opening_time, date_of_creation, timestamp,
                             in_game_name_of_creator, locationality, directionality,
                             works_in, link_to_image, link_to_youtube_video,
                             link_to_world_download, server_ip, coordinates,
                             command_to_get_to_build, your_ign_or_discord])
        await message.edit(content='Record submitted successfully!')
