"""Models and views for discord interactions."""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import TYPE_CHECKING, Sequence, cast, override

import discord
from discord import Interaction

from squid.bot.submission import ui
from squid.bot.submission.navigation_view import BaseNavigableView, MaybeAwaitableBaseNavigableViewFunc
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.components import (
    BuildField,
    DirectonalityLocationalitySelect,
    DoorTypeSelect,
    DynamicBuildEditButton,
    EphemeralBuildEditButton,
    RecordCategorySelect,
    get_text_input,
)
from squid.bot.utils import DEFAULT, DefaultType
from squid.db.builds import Build
from squid.db.schema import Category, Status

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid
    from squid.bot.submission.build_handler import BuildHandler


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

        self.build.door_dimensions = parse_hallway_dimensions(self.door_size.value)
        self.build.door_type = self.pattern.value.split(", ") if self.pattern.value else ["Regular"]
        self.build.dimensions = parse_dimensions(self.dimensions.value)
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


class EditModal[BotT: RedstoneSquid](discord.ui.Modal):
    """This is a modal that allows users to edit a build. Exclusively for BuildEditView."""

    def __init__(
        self, parent: ui.views.BuildEditView[BotT], title: str, timeout: float | None = 60, custom_id: str | None = None
    ):
        self.parent = parent
        if custom_id:
            super().__init__(title=title, timeout=timeout, custom_id=custom_id)
        else:
            super().__init__(title=title, timeout=timeout)

    @override
    async def on_submit(self, interaction: discord.Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        # Update the build object with the new values
        await asyncio.gather(*(item.on_modal_submit() for item in self.children if isinstance(item, BuildField)))
        await self.parent.update(interaction)


class BuildSubmissionForm(discord.ui.View):
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


class ConfirmationView(discord.ui.View):
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


class BuildEditView[BotT: RedstoneSquid](discord.ui.View):
    """A view that allows users to edit a build.

    This view has no locking guarantees and may fail if the user submits a build that has been locked by another task/thread/process.
    """
    def __init__(
        self,
        build: Build,
        items: Sequence[BuildField] | DefaultType = DEFAULT,
        *,
        timeout: float = 300,
    ):
        """Initializes the BuildEditView.

        Args:
            build: The build to edit.
            items: The items to display in the view.
            parent: The parent view.
            timeout: The timeout for the view.
        """
        super().__init__(timeout=timeout)
        self.timeout = cast(float, self.timeout)
        self.build = build
        if items is DEFAULT:
            items = [
                get_text_input(build, "dimensions", placeholder="Width x Height x Depth", required=True),
                get_text_input(build, "door_dimensions", placeholder="2x2", required=True),
                get_text_input(build, "version_spec", placeholder="1.16 - 1.17.3"),
                get_text_input(build, "door_type", placeholder="Full lamp, Funnel"),
                get_text_input(build, "door_orientation_type", placeholder="Door, Trapdoor, Skydoor"),
                get_text_input(build, "wiring_placement_restrictions", placeholder="Seamless, Full Flush"),
                get_text_input(build, "component_restrictions", placeholder="Observerless"),
                get_text_input(build, "miscellaneous_restrictions", placeholder="Directional, Locational"),
                get_text_input(build, "normal_closing_time", placeholder="in gameticks"),
                get_text_input(build, "normal_opening_time", placeholder="in gameticks"),
                get_text_input(build, "creators_ign", placeholder="Me, My Dog"),
                get_text_input(build, "image_urls", placeholder="any urls, comma separated"),
                get_text_input(build, "video_urls", placeholder="any urls, comma separated"),
                get_text_input(build, "world_download_urls", placeholder="any urls, comma separated"),
                get_text_input(build, "extra_info", placeholder="TODO: Explain this format"),
                get_text_input(build, "completion_time", placeholder="Any time format works"),
                get_text_input(build, "ai_generated", placeholder="True/False"),
            ]
        self.items = items
        self.page = 1
        self._max_pages = len(self.items) // 5 + 1
        self.expiry_time: datetime.datetime = discord.utils.utcnow() + datetime.timedelta(seconds=self.timeout)

    @override
    async def interaction_check(self, interaction: Interaction[BotT], /) -> bool:  # pyright: ignore [reportIncompatibleMethodOverride]
        if discord.utils.utcnow() > self.expiry_time:
            for item in self.children:
                item.disabled = True  # type: ignore
            await interaction.followup.send("This edit session has expired. Your edits are not saved.", ephemeral=True)
            return False
        return True

    def get_modal(self) -> EditModal:
        """Page is 1-indexed"""
        modal = EditModal(
            parent=self,
            title=f"Edit Build (Page {self.page})",
            timeout=(self.expiry_time - discord.utils.utcnow()).seconds,
        )
        if 5 * self.page <= len(self.items):
            for i in range(5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i])
        else:
            for i in range(len(self.items) % 5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i])
        return modal

    def _handle_button_states(self) -> None:
        self.previous_page.disabled = self.page == 1
        self.next_page.disabled = self.page == self._max_pages

    async def send(self, interaction: discord.Interaction[BotT], ephemeral: bool = False) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        self._handle_button_states()
        await interaction.followup.send(
            f"Page {self.page}/{self._max_pages}",
            view=self,
            embeds=await self.get_embeds(interaction),
            ephemeral=ephemeral,
        )

    async def update(self, interaction: discord.Interaction[BotT]):
        self._handle_button_states()
        await interaction.response.edit_message(
            content=f"Page {self.page}/{self._max_pages}", view=self, embeds=await self.get_embeds(interaction)
        )

    def get_handler(self, interaction: discord.Interaction[BotT]) -> BuildHandler[BotT]:
        return interaction.client.for_build(self.build)

    async def get_embeds(self, interaction: discord.Interaction[BotT]) -> list[discord.Embed]:
        return [self.summary_embed, await self.get_handler(interaction).generate_embed()]

    @property
    def summary_embed(self) -> discord.Embed:
        summaries = [item.summary for item in self.items]
        for i in range(5 * (self.page - 1), min(len(self.items), 5 * self.page)):
            summaries[i] = f"**{summaries[i]}**"
        return discord.Embed(title="Build Summary", description="\n".join(summaries))

    @discord.ui.button(label="Open", style=discord.ButtonStyle.primary)
    async def open(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        await interaction.response.send_modal(self.get_modal())

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page -= 1
        self._handle_button_states()
        await self.update(interaction)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page += 1
        self._handle_button_states()
        await self.update(interaction)

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        await interaction.response.defer()
        await self.build.save()
        await interaction.followup.send(
            content="Submitted", embed=await self.get_handler(interaction).generate_embed(), ephemeral=True
        )


class BuildInfoView[BotT: RedstoneSquid](BaseNavigableView[BotT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[BotT] | MaybeAwaitableBaseNavigableViewFunc[BotT] | None = None,
    ):
        super().__init__(parent=parent, timeout=None)
        self.build = build
        if build.id is None:
            self.add_item(EphemeralBuildEditButton(build))
        else:
            self.add_item(DynamicBuildEditButton(build))

    async def get_embed(self, interaction: discord.Interaction[BotT]) -> discord.Embed:
        return await interaction.client.for_build(self.build).generate_embed()

    @override
    async def send(self, interaction: discord.Interaction[BotT]) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await interaction.followup.send(embed=await self.get_embed(interaction), view=self)

    @override
    async def update(self, interaction: discord.Interaction[BotT]) -> None:
        await interaction.response.edit_message(content=None, embed=await self.get_embed(interaction), view=self)
