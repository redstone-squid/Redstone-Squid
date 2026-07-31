"""Models and views for discord interactions."""

import asyncio
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast, override

import discord
from discord import Interaction
from whenever import Instant

from squid.bot.errors import ErrorHandledLayoutView, ErrorHandledModal
from squid.bot.i18n import t
from squid.bot.submission.navigation_view import BaseNavigableView, MaybeAwaitableBaseNavigableViewFunc
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.components import (
    BuildField,
    DirectonalityLocationalitySelect,
    DoorTypeSelect,
    DynamicBuildEditButton,
    EphemeralBuildEditButton,
    get_text_input,
)
from squid.bot.utils.components import (
    StaticLayout,
    card_container,
    edit_interaction_layout,
    no_mentions,
)
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import Build, BuildCategory, Status
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app
    import squid.bot.submission.build_handler


class SubmissionModal(ErrorHandledModal):
    def __init__(self, build: Build, builds: BuildService):
        super().__init__(title="Submit Your Build")
        self.build = build
        self.builds = builds

        # Door size
        self.door_size = discord.ui.TextInput(placeholder="e.g. 2x2 (piston door)")

        # Pattern
        self.pattern = discord.ui.TextInput(placeholder="e.g. full lamp, funnel", required=False)

        # Dimensions
        self.dimensions = discord.ui.TextInput(placeholder="Width x Height x Depth", required=True)

        # Versions
        self.versions = discord.ui.TextInput(placeholder="e.g., 1.16.1, 1.17.3", required=False)

        # Restrictions
        self.restrictions = discord.ui.TextInput(
            placeholder="e.g., Seamless, Full Flush",
            required=False,
        )

        # Additional Information
        self.additional_info = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            required=False,
        )

        self.add_item(discord.ui.Label(text="Door Size", component=self.door_size))
        self.add_item(discord.ui.Label(text="Pattern Type", component=self.pattern))
        self.add_item(discord.ui.Label(text="Dimensions", component=self.dimensions))
        self.add_item(discord.ui.Label(text="Restrictions", component=self.restrictions))
        self.add_item(discord.ui.Label(text="Additional Information", component=self.additional_info))

    @override
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()  # type: ignore

        self.build.door_dimensions = parse_hallway_dimensions(self.door_size.value)
        self.build.door_type = self.pattern.value.split(", ") if self.pattern.value else ["Regular"]
        self.build.dimensions = parse_dimensions(self.dimensions.value)
        await self.builds.classify_restrictions(self.build, self.restrictions.value.split(", "))

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


class EditModal[BotT: "squid.bot.app.RedstoneSquid"](ErrorHandledModal):
    """This is a modal that allows users to edit a build. Exclusively for BuildEditView."""

    def __init__(
        self, parent: "BuildEditView[BotT]", title: str, timeout: float | None = 60, custom_id: str | None = None
    ):
        self.parent = parent
        if custom_id:
            super().__init__(title=title, timeout=timeout, custom_id=custom_id)
        else:
            super().__init__(title=title, timeout=timeout)

    @override
    async def on_submit(self, interaction: discord.Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        # Update the build object with the new values
        await asyncio.gather(*(item.on_modal_submit() for item in self.walk_children() if isinstance(item, BuildField)))
        await self.parent.update(interaction)


class BuildSubmissionForm(ErrorHandledLayoutView):
    actions = discord.ui.ActionRow()

    def __init__(self, build: Build, builds: BuildService, *, timeout: float | None = 180.0):
        super().__init__(timeout=timeout)
        # Assumptions
        build.submission_status = Status.PENDING
        build.category = BuildCategory.DOOR

        self.build = build
        self.builds = builds
        self.value = None
        controls = self.actions
        self.clear_items()
        self.add_item(discord.ui.TextDisplay("Use the select menus, then submit or cancel."))
        self.add_item(discord.ui.ActionRow(DoorTypeSelect(self.build)))
        self.add_item(discord.ui.ActionRow(DirectonalityLocationalitySelect(self.build)))
        self.add_item(controls)

    @actions.button(label="Submit", style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.build.submitter_id = interaction.user.id
        self.value = True
        self.stop()

    @actions.button(label="Add more Information", custom_id="open_modal", style=discord.ButtonStyle.primary)
    async def add_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SubmissionModal(self.build, self.builds))

    @actions.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        self.stop()


class ConfirmationView(ErrorHandledLayoutView):
    """A simple Yes/No style pair of buttons for confirming an action.

    `prompt` should already be translated by the caller (it has the invocation
    context this view doesn't); `locale` only covers this view's own button labels.
    """

    actions = discord.ui.ActionRow()

    def __init__(self, prompt: str | None = None, timeout: int = 60, *, locale: str | None = None):
        super().__init__(timeout=timeout)
        self.value = None
        controls = self.actions
        self.clear_items()
        self.add_item(discord.ui.TextDisplay(prompt or t(locale, _("Confirm this action?"))))
        self.add_item(controls)
        self.confirm.label = t(locale, _("Confirm"))
        self.cancel.label = t(locale, _("Cancel"))

    @actions.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @actions.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()


class BuildEditView[BotT: "squid.bot.app.RedstoneSquid"](ErrorHandledLayoutView):
    """A view that allows users to edit a build.

    Changes are accumulated locally and applied under a service-managed lock on submit.
    """

    actions = discord.ui.ActionRow()

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        items: Sequence[BuildField[Any]] | DefaultType = DEFAULT,
        *,
        timeout: float = 300,
    ):
        """Initializes the BuildEditView.

        Args:
            build: The build to edit.
            items: The items to display in the view.
            timeout: The timeout for the view.
        """
        super().__init__(timeout=timeout)
        self.build = build
        self.builds = builds
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
        self.expiry_time = Instant.now().add(seconds=timeout)

    @override
    async def interaction_check(self, interaction: Interaction[BotT], /) -> bool:  # pyright: ignore [reportIncompatibleMethodOverride]
        if Instant.now() > self.expiry_time:
            for item in self.walk_children():
                if isinstance(item, discord.ui.Button | discord.ui.Select):
                    item.disabled = True
            await interaction.followup.send(
                view=StaticLayout(discord.ui.TextDisplay("This edit session has expired. Your edits are not saved.")),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return False
        return True

    def get_modal(self) -> EditModal:
        """Page is 1-indexed"""
        modal = EditModal(
            parent=self,
            title=f"Edit Build (Page {self.page})",
            timeout=max(0.0, (self.expiry_time - Instant.now()).total("seconds")),
        )
        if 5 * self.page <= len(self.items):
            for i in range(5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i].to_label())
        else:
            for i in range(len(self.items) % 5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i].to_label())
        return modal

    def _handle_button_states(self) -> None:
        self.previous_page.disabled = self.page == 1
        self.next_page.disabled = self.page == self._max_pages

    async def send(self, interaction: discord.Interaction[BotT], ephemeral: bool = False) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        self._handle_button_states()
        await self._render(interaction)
        await interaction.followup.send(
            view=self,
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
        )

    async def update(self, interaction: discord.Interaction[BotT]):
        self._handle_button_states()
        await self._render(interaction)
        await edit_interaction_layout(interaction, self)

    def get_handler(
        self, interaction: discord.Interaction[BotT]
    ) -> "squid.bot.submission.build_handler.BuildHandler[BotT]":
        return interaction.client.for_build(self.build)

    def summary_text(self) -> str:
        summaries = [item.summary for item in self.items]
        for i in range(5 * (self.page - 1), min(len(self.items), 5 * self.page)):
            summaries[i] = f"**{summaries[i]}**"
        return "\n".join(summaries)

    async def _render(self, interaction: discord.Interaction[BotT]) -> None:
        controls = self.actions
        self.clear_items()
        self.add_item(discord.ui.TextDisplay(f"Page {self.page}/{self._max_pages}"))
        self.add_item(card_container("Build Summary", self.summary_text()))
        self.add_item(await self.get_handler(interaction).render_container())
        self.add_item(controls)

    @actions.button(label="Open", style=discord.ButtonStyle.primary)
    async def open(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        await interaction.response.send_modal(self.get_modal())

    @actions.button(label="Previous Page", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page -= 1
        self._handle_button_states()
        await self.update(interaction)

    @actions.button(label="Next Page", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page += 1
        self._handle_button_states()
        await self.update(interaction)

    @actions.button(label="Submit", style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        await interaction.response.defer()
        patch = BuildEditPatch.from_attributes(
            {item.attribute: item.actual_value for item in self.items if item.modified}
        )
        if self.build.id is None:
            patch.apply(self.build)
            await self.builds.save(self.build)
        else:
            async with self.builds.edit(self.build.id, patch) as edit:
                self.build = await edit.commit()
        await interaction.followup.send(
            view=StaticLayout(
                discord.ui.TextDisplay("Submitted"),
                await self.get_handler(interaction).render_container(),
            ),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )


class BuildInfoView[BotT: "squid.bot.app.RedstoneSquid"](BaseNavigableView[BotT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[BotT] | MaybeAwaitableBaseNavigableViewFunc[BotT] | None = None,
    ):
        super().__init__(parent=parent, timeout=None)
        self.build = build
        if build.id is None:
            edit_button = EphemeralBuildEditButton(build)
        else:
            edit_button = DynamicBuildEditButton(build)
        self._edit_row = discord.ui.ActionRow(
            cast(discord.ui.Item[discord.ui.LayoutView], edit_button),
        )
        self.add_item(self._edit_row)

    async def _render(self, interaction: discord.Interaction[BotT]) -> None:
        self.clear_items()
        self.add_item(await interaction.client.for_build(self.build).render_container())
        self.add_item(self._edit_row)
        self.add_item(self._navigation_row)

    @override
    async def send(self, interaction: discord.Interaction[BotT]) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self._render(interaction)
        await interaction.followup.send(view=self, allowed_mentions=no_mentions())

    @override
    async def update(self, interaction: discord.Interaction[BotT]) -> None:
        await self._render(interaction)
        await edit_interaction_layout(interaction, self)
