"""Models, view and buttons for the submission process."""

from __future__ import annotations

import logging
import re
import json
from typing import Sequence, override, cast, TYPE_CHECKING, Self, Any

import discord
from beartype.door import is_bearable, is_subhint
from discord import Interaction
from discord._types import ClientT
from discord.ui import Item

from squid.bot.submission.navigation_view import BaseNavigableView, MaybeAwaitableBaseNavigableViewFunc
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.database.builds import Build
from squid.database.schema import RECORD_CATEGORIES, DOOR_ORIENTATION_NAMES, Status, Category


if TYPE_CHECKING:
    # importing this causes a circular import at runtime
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


class EditModal(discord.ui.Modal):
    def __init__(self, parent: EditView, title: str, timeout: float | None = 60, custom_id: str | None = None):
        self.parent = parent
        if custom_id:
            super().__init__(title=title, timeout=timeout, custom_id=custom_id)
        else:
            super().__init__(title=title, timeout=timeout)

    @override
    async def on_submit(self, interaction: discord.Interaction):
        for item in self.children:
            if isinstance(item, BuildField):
                await item.callback(interaction)
        await self.parent.update(interaction)


class BuildField(discord.ui.TextInput):
    def __init__(
        self,
        build: Build,
        attribute: str,
        attr_type: type[Any],
        *,
        label: str | None = None,
        placeholder: str | None = None,
        required: bool = False,
        default: str | None = None,
    ):
        try:
            value = getattr(build, attribute)
        except AttributeError:
            value = ""
        if not is_bearable(value, attr_type):
            logging.error(f"Invalid hint for {attribute}: {attr_type}")

        if value is None:
            value = ""
        elif attr_type is str:
            pass
        elif attr_type is int:
            value = str(value)
        elif is_subhint(attr_type, list):
            value = ", ".join(value)
        elif is_subhint(attr_type, Sequence[int | None]):
            value = f"{value[0] or '?'}x{value[1] or '?'}x{value[2] or '?'}"
        self.build = build
        self.attribute = attribute
        self.attr_type = attr_type
        super().__init__(
            label=label or attribute.replace("_", " ").title(),
            default=default or str(value),
            required=required,
            placeholder=placeholder,
        )

    @override
    async def callback(self, interaction: Interaction[ClientT]) -> None:
        self.default = self.value
        try:
            if self.attr_type is str:
                value = self.value
            elif self.attr_type is int:
                value = int(self.value)
            elif self.attr_type is bool:
                value = self.value.lower() in ("true", "yes", "1")
            elif self.attr_type == tuple[int | None, int | None, int | None]:
                value = parse_dimensions(self.value)
            elif self.attr_type == list[str]:
                value = [item.strip() for item in self.value.split(", ")]
                value = [item for item in value if item]
            elif self.attr_type == dict[str, Any]:
                value = json.loads(self.value)
            else:
                raise NotImplementedError(f"Unsupported type {self.attr_type}")
        except Exception:
            return

        try:
            logging.info(f"Trying to set {self.attribute} to {value}")
            setattr(self.build, self.attribute, value)
        except (AttributeError, ValueError):
            pass

    @property
    def summary(self) -> str:
        return f"{self.label}: {self.value}"


class EditView[ClientT: discord.Client](BaseNavigableView[ClientT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT] | None = None,
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
    async def send(self, interaction: discord.Interaction[ClientT], ephemeral: bool = False) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        self._handle_button_states()
        await interaction.followup.send(
            f"Page {self.page}/{self._max_pages}", view=self, embeds=await self.get_embeds(), ephemeral=ephemeral
        )

    @override
    async def update(self, interaction: discord.Interaction[ClientT]):
        self._handle_button_states()
        await interaction.response.edit_message(
            content=f"Page {self.page}/{self._max_pages}", view=self, embeds=await self.get_embeds()
        )

    async def get_embeds(self) -> list[discord.Embed]:
        return [self.summary_embed(), await self.build.generate_embed()]

    def summary_embed(self) -> discord.Embed:
        summaries = [item.summary for item in self.items]
        for i in range(5 * (self.page - 1), min(len(self.items), 5 * self.page)):
            summaries[i] = f"**{summaries[i]}**"
        return discord.Embed(title="Build Summary", description="\n".join(summaries))

    @discord.ui.button(label="Open", style=discord.ButtonStyle.primary)
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(self.get_modal())

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction[ClientT], button: discord.ui.Button):
        self.page -= 1
        self._handle_button_states()
        await self.update(interaction)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction[ClientT], button: discord.ui.Button):
        self.page += 1
        self._handle_button_states()
        await self.update(interaction)

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction[ClientT], button: discord.ui.Button):
        await self.press_home(interaction)
        await self.build.save()
        await interaction.followup.send(content="Submitted", embed=await self.build.generate_embed(), ephemeral=True)


class BuildInfoView[ClientT: discord.Client](BaseNavigableView[ClientT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT] | None = None,
    ):
        super().__init__(parent=parent, timeout=None)
        self.build = build
        if build.id is None:
            self.add_item(EphemeralBuildEditButton(build))
        else:
            self.add_item(DynamicBuildEditButton(build))

    async def get_embed(self) -> discord.Embed:
        return await self.build.generate_embed()

    @override
    async def send(self, interaction: discord.Interaction[ClientT]) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await interaction.followup.send(embed=await self.get_embed(), view=self)

    @override
    async def update(self, interaction: discord.Interaction[ClientT]) -> None:
        await interaction.response.edit_message(content=None, embed=await self.get_embed(), view=self)


class DynamicBuildEditButton(discord.ui.DynamicItem[discord.ui.Button], template=r"edit:build:(\d+)"):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(
            discord.ui.Button(
                label="Edit",
                style=discord.ButtonStyle.secondary,
                custom_id=f"edit:build:{build.id}",
            )
        )

    @classmethod
    @override
    async def from_custom_id(
        cls: type[Self], interaction: Interaction[ClientT], item: Item[Any], match: re.Match[str], /
    ) -> Self:
        build = await Build.from_id(int(match.group(1)))
        assert build is not None
        return cls(build)

    @override
    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        async def _parent() -> BuildInfoView[ClientT]:
            # await self.build.reload()  # TODO: reload() is not implemented
            build = await self.build.get_persisted_copy()
            return BuildInfoView(build)

        await EditView(self.build, parent=_parent).update(interaction)


class EphemeralBuildEditButton(discord.ui.Button):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(label="Edit", style=discord.ButtonStyle.secondary)

    @override
    async def callback(self, interaction: Interaction[ClientT]) -> None:
        await EditView(self.build).send(interaction, ephemeral=True)
