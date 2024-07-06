# FIXME: this file name can't be worse bcs dpy has a utils file
from traceback import format_tb
from types import TracebackType
from typing import List, Tuple
import re

from discord.interactions import InteractionResponse

import discord
from discord.ext.commands import Context
from discord.ui import View, Select, Button
from discord.utils import MISSING

from Discord.config import OWNER_ID, PRINT_TRACEBACKS

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def error_embed(title, description):
    return discord.Embed(
        title=title, colour=discord_red, description=":x: " + description
    )


def warning_embed(title, description):
    return discord.Embed(
        title=":warning: " + title, colour=discord_yellow, description=description
    )


def info_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


def parse_door_size(size_str: str) -> Tuple[int, int, int | None]:
    inputs = size_str.split("x")
    if not 2 <= len(inputs) <= 3:
        raise ValueError(
            "Invalid door size. Must be in the format 'width x height' or 'width x height x depth'"
        )

    try:
        dimensions = list(map(int, inputs))
    except ValueError:
        raise ValueError(
            f"Invalid door size. Each dimension must be parsable an integer, found {inputs}"
        )

    if len(dimensions) == 2:
        return dimensions[0], dimensions[1], None
    elif len(dimensions) == 3:
        return dimensions[0], dimensions[1], dimensions[2]


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.sent_message = None
        self.delete_on_exit = delete_on_exit

    async def __aenter__(self):
        self.sent_message = await self.ctx.send(
            embed=info_embed(self.title, self.description)
        )
        return self.sent_message

    async def __aexit__(self, exc_type, exc_val, exc_tb: TracebackType):
        # Handle exceptions
        if exc_type is not None:
            description = f"{str(exc_val)}"
            if PRINT_TRACEBACKS:
                description += f'\n\n```{"".join(format_tb(exc_tb))}```'
            await self.sent_message.edit(
                content=f"<@{OWNER_ID}>",
                embed=error_embed(
                    f"An error has occurred: {exc_type.__name__}", description
                ),
            )
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False


class ConfirmationView(View):
    def __init__(self, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()


class SubmissionModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Submit Your Build")

        # Door size
        self.door_size = discord.ui.TextInput(
            label="Door Size", placeholder="e.g., 2x2 piston door"
        )

        # Pattern
        self.pattern = discord.ui.TextInput(
            label="Pattern Type", placeholder="e.g., full lamp, funnel", required=False
        )

        # Dimensions
        self.dimensions = discord.ui.TextInput(
            label="Dimensions", placeholder="Width x Height x Depth", required=False
        )

        # Restrictions
        self.restrictions = discord.ui.TextInput(
            label="Restrictions",
            placeholder="e.g., Seamless, Full Flush",
            required=False,
        )

        # Additional Information
        self.additional_info = discord.ui.TextInput(
            label="Additional Information",
            style=discord.TextStyle.paragraph,
            required=False,
        )

        self.add_item(self.door_size)
        self.add_item(self.pattern)
        self.add_item(self.dimensions)
        self.add_item(self.restrictions)
        self.add_item(self.additional_info)

    async def on_submit(self, interaction: discord.Interaction):
        dimensions = parse_door_size(self.dimensions.value)
        width = dimensions[0]
        height = dimensions[1]
        depth = dimensions[2]

        pattern_match = re.search(
            r"\bpattern:\s*([^,]+)(?:,|$)", self.additional_info.value, re.IGNORECASE
        )
        pattern = pattern_match.group(1).strip() if pattern_match else None

        # Extract IGN
        ign_match = re.search(
            r"\bign:\s*([^,]+)(?:,|$)", self.additional_info.value, re.IGNORECASE
        )
        ign = ign_match.group(1).strip() if ign_match else None

        # Extract video link
        video_match = re.search(
            r"\bvideo:\s*(https?://[^\s,]+)(?:,|$)",
            self.additional_info.value,
            re.IGNORECASE,
        )
        video_link = video_match.group(1).strip() if video_match else None

        # Extract download link
        download_match = re.search(
            r"\bdownload:\s*(https?://[^\s,]+)(?:,|$)",
            self.additional_info.value,
            re.IGNORECASE,
        )
        download_link = download_match.group(1).strip() if download_match else None
        parsed_children = {
            "parse_door_size": self.door_size.value,
            "parse_pattern": self.pattern.value,
            "parse_dimensions": self.dimensions.value,
            "parse_restrictions": self.restrictions.value,
            "parse_additional_info": self.additional_info.value,
        }


class OpenModalButton(Button):
    def __init__(self):
        super().__init__(
            label="Open Modal",
            style=discord.ButtonStyle.primary,
            custom_id="open_modal",
        )

    async def callback(self, interaction: discord.Interaction):
        interaction_response: InteractionResponse = interaction.response  # type: ignore
        await interaction_response.send_modal(SubmissionModal())


class RecordCategory(discord.ui.Select):
    def __init__(self):

        # Set the options that will be presented inside the dropdown
        options = [
            discord.SelectOption(label="Smallest"),
            discord.SelectOption(label="Fastest"),
            discord.SelectOption(label="First"),
        ]

        super().__init__(
            placeholder="Choose the record category",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore


class DoorType(discord.ui.Select):
    def __init__(self):

        # Set the options that will be presented inside the dropdown
        options = [
            discord.SelectOption(label="Door"),
            discord.SelectOption(label="Skydoor"),
            discord.SelectOption(label="Trapdoor"),
        ]

        super().__init__(
            placeholder="Choose the door type",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore


class VersionsSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Pre 1.5"),
            discord.SelectOption(label="1.5"),
            discord.SelectOption(label="1.6"),
            discord.SelectOption(label="1.7"),
            discord.SelectOption(label="1.8"),
            discord.SelectOption(label="1.9"),
            discord.SelectOption(label="1.10"),
            discord.SelectOption(label="1.11"),
            discord.SelectOption(label="1.12"),
            discord.SelectOption(label="1.13"),
            discord.SelectOption(label="1.13.1 / 1.13.2"),
            discord.SelectOption(label="1.14"),
            discord.SelectOption(label="1.14.1"),
            discord.SelectOption(label="1.15"),
            discord.SelectOption(label="1.16"),
            discord.SelectOption(label="1.17"),
            discord.SelectOption(label="1.18"),
            discord.SelectOption(label="1.19"),
            discord.SelectOption(label="1.20"),
            discord.SelectOption(label="1.20.4"),
        ]

        super().__init__(
            placeholder="Choose the versions the door works in",
            min_values=1,
            max_values=19,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore


class DirectonalityLocationalitySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Directional"),
            discord.SelectOption(label="Locational"),
            discord.SelectOption(label="Fully reliable"),
        ]

        super().__init__(
            placeholder="Choose how reliable the the door is",
            min_values=1,
            max_values=2,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore

class BuildSubmissionForm(View):
    def __init__(self):
        super().__init__()
        self.add_item(RecordCategory())
        self.add_item(DoorType())
        self.add_item(VersionsSelect())
        self.add_item(DirectonalityLocationalitySelect())
        self.add_item(OpenModalButton())
