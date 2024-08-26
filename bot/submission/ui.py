"""Models, view and buttons for the submission process."""

from __future__ import annotations

import re
from typing import override, cast, TYPE_CHECKING

import discord
from discord import InteractionResponse
from discord.ui import Button, View

from bot import utils as utils, config as config
from database.builds import Build
from database.enums import Status, Category
from database.schema import RECORD_CATEGORIES, DOOR_ORIENTATION_NAMES

if TYPE_CHECKING:
    from discord.types.interactions import SelectMessageComponentInteractionData


class SubmissionModal(discord.ui.Modal):
    def __init__(self, build: Build):
        super().__init__(title="Submit Your Build")
        self.build = build

        # Door size
        self.door_size = discord.ui.TextInput(label="Door Size", placeholder="e.g. 2x2 (piston door)")

        # Pattern
        self.pattern = discord.ui.TextInput(label="Pattern Type", placeholder="e.g. full lamp, funnel", required=False)

        # Dimensions
        self.dimensions = discord.ui.TextInput(label="Dimensions", placeholder="Width x Height x Depth", required=True)

        # Versions
        self.versions = discord.ui.TextInput(label="Versions", placeholder="e.g., 1.16.1, 1.17.3", required=False)

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

    @override
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore

        self.build.door_dimensions = utils.parse_hallway_dimensions(self.door_size.value)
        self.build.door_type = self.pattern.value.split(", ") if self.pattern.value else ["Regular"]
        self.build.dimensions = utils.parse_dimensions(self.dimensions.value)  # type: ignore
        await self.build.set_restrictions(self.restrictions.value.split(", "))

        # Extract IGN
        ign_match = re.search(r"\bign:\s*([^,]+)(?:,|$)", self.additional_info.value, re.IGNORECASE)
        if ign_match:
            igns = ign_match.groups()
            self.build.creators_ign = [ign.strip() for ign in igns]

        # Extract video link
        video_match = re.search(
            r"\bvideo:\s*(https?://[^\s,]+)(?:,|$)",
            self.additional_info.value,
            re.IGNORECASE,
        )
        if video_match:
            video_links = video_match.groups()
            self.build.video_urls = [video_link.strip() for video_link in video_links]

        # Extract download link
        download_match = re.search(
            r"\bdownload:\s*(https?://[^\s,]+)(?:,|$)",
            self.additional_info.value,
            re.IGNORECASE,
        )
        if download_match:
            download_links = download_match.groups()
            self.build.world_download_urls = [download_link.strip() for download_link in download_links]


class RecordCategorySelect(discord.ui.Select):
    def __init__(self, build: Build):
        self.build = build

        options = [discord.SelectOption(label=category) for category in RECORD_CATEGORIES]
        super().__init__(
            placeholder="Choose the record category",
            min_values=1,
            max_values=1,
            options=options,
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        data = cast("SelectMessageComponentInteractionData", interaction.data)
        self.build.record_category = data["values"][0]  # type: ignore
        await interaction.response.defer()  # type: ignore


class DoorTypeSelect(discord.ui.Select):
    def __init__(self, build: Build):
        self.build = build

        options = [discord.SelectOption(label=door_type) for door_type in DOOR_ORIENTATION_NAMES]
        super().__init__(
            placeholder="Choose the door type",
            min_values=1,
            max_values=1,
            options=options,
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        data = cast("SelectMessageComponentInteractionData", interaction.data)
        self.build.door_orientation_type = data["values"][0]  # type: ignore
        await interaction.response.defer()  # type: ignore


class DirectonalityLocationalitySelect(discord.ui.Select):
    def __init__(self, build: Build):
        self.build = build

        options = [
            discord.SelectOption(label="Directional"),
            discord.SelectOption(label="Locational"),
        ]

        super().__init__(
            placeholder="Choose how reliable the the door is",
            min_values=0,
            max_values=2,
            options=options,
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        data = cast("SelectMessageComponentInteractionData", interaction.data)
        self.build.miscellaneous_restrictions = data["values"]
        await interaction.response.defer()  # type: ignore


class BuildSubmissionForm(View):
    def __init__(self, build: Build, *, timeout: float | None = 180.0):
        super().__init__(timeout=timeout)
        # Assumptions
        build.submission_status = Status.PENDING
        build.category = Category.DOOR

        self.build = build
        self.value = None
        self.add_item(RecordCategorySelect(self.build))
        self.add_item(DoorTypeSelect(self.build))
        self.add_item(DirectonalityLocationalitySelect(self.build))

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.build.submitter_id = interaction.user.id
        self.value = True
        self.stop()

    @discord.ui.button(label="Add more Information", custom_id="open_modal", style=discord.ButtonStyle.primary)
    async def add_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SubmissionModal(self.build))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        self.stop()


class ConfirmationView(View):
    """A simple Yes/No style pair of buttons for confirming an action."""

    def __init__(self, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
