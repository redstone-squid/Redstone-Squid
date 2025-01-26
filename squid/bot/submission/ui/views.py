from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, override

import discord

from squid.bot.submission.navigation_view import BaseNavigableView, MaybeAwaitableBaseNavigableViewFunc
from squid.bot.submission.ui.components import (
    BuildField,
    DirectonalityLocationalitySelect,
    DoorTypeSelect,
    DynamicBuildEditButton,
    EditModal,
    EphemeralBuildEditButton,
    RecordCategorySelect,
    SubmissionModal,
)
from squid.db.builds import Build
from squid.db.schema import Status, Category

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


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


class BuildEditView[BotT: RedstoneSquid](BaseNavigableView[BotT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[BotT] | MaybeAwaitableBaseNavigableViewFunc[BotT] | None = None,
    ):
        super().__init__(parent=parent, timeout=None)
        self.build = build
        self.items: list[BuildField] = [
            BuildField(
                build,
                "dimensions",
                tuple[int | None, int | None, int | None],
                placeholder="Width x Height x Depth",
                default=f"{build.width}x{build.height}x{build.depth}",
                required=True,
            ),
            BuildField(
                build,
                "door_dimensions",
                tuple[int | None, int | None, int | None],
                placeholder="2x2",
                default=f"{build.door_width}x{build.door_height}x{build.door_depth}",
                required=True,
            ),
            BuildField(build, "version_spec", str, label="Versions", placeholder="1.16 - 1.17.3"),
            BuildField(build, "door_type", list[str], label="Pattern Type", placeholder="full lamp, funnel"),
            BuildField(build, "door_orientation_type", str, label="Orientation"),
            BuildField(build, "wiring_placement_restrictions", list[str], placeholder="Seamless, Full Flush"),
            BuildField(build, "component_restrictions", list[str]),
            BuildField(build, "miscellaneous_restrictions", list[str]),
            BuildField(build, "normal_closing_time", int),
            BuildField(build, "normal_opening_time", int),
            BuildField(build, "creators_ign", str),
            BuildField(build, "image_urls", list[str]),
            BuildField(build, "video_urls", list[str]),
            BuildField(build, "world_download_urls", list[str]),
            BuildField(build, "server_info", dict[str, Any]),
            BuildField(build, "completion_time", str),
            BuildField(build, "ai_generated", bool),
        ]
        self.page = 1
        self._max_pages = len(self.items) // 5 + 1

    def get_modal(self) -> discord.ui.Modal:
        """Page is 1-indexed"""
        modal = EditModal(parent=self, title=f"Edit Build (Page {self.page})", timeout=None)
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

    @override
    async def send(self, interaction: discord.Interaction[BotT], ephemeral: bool = False) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        self._handle_button_states()
        await interaction.followup.send(
            f"Page {self.page}/{self._max_pages}", view=self, embeds=await self.get_embeds(interaction), ephemeral=ephemeral
        )

    @override
    async def update(self, interaction: discord.Interaction[BotT]):
        self._handle_button_states()
        await interaction.response.edit_message(
            content=f"Page {self.page}/{self._max_pages}", view=self, embeds=await self.get_embeds(interaction)
        )

    async def get_embeds(self, interaction: discord.Interaction[BotT]) -> list[discord.Embed]:
        return [self.summary_embed(), await interaction.client.for_build(self.build).generate_embed()]

    def summary_embed(self) -> discord.Embed:
        summaries = [item.summary for item in self.items]
        for i in range(5 * (self.page - 1), min(len(self.items), 5 * self.page)):
            summaries[i] = f"**{summaries[i]}**"
        return discord.Embed(title="Build Summary", description="\n".join(summaries))

    @discord.ui.button(label="Open", style=discord.ButtonStyle.primary)
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        await self.press_home(interaction)
        await self.build.save()
        await interaction.followup.send(content="Submitted", embed=await interaction.client.for_build(self.build).generate_embed(), ephemeral=True)


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
