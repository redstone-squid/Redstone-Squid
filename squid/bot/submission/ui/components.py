"""Models, view and buttons for the submission process."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Self, Sequence, cast, override

import discord
from beartype.door import is_bearable, is_subhint
from discord import Interaction
from discord.ui import Item

from squid.bot.submission.navigation_view import BaseNavigableView, MaybeAwaitableBaseNavigableViewFunc
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission import ui
from squid.db.builds import Build
from squid.db.schema import DOOR_ORIENTATION_NAMES, RECORD_CATEGORIES, Category, Status

if TYPE_CHECKING:
    from squid.bot.main import RedstoneSquid
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


class EditModal(discord.ui.Modal):
    def __init__(self, parent: ui.views.BuildEditView, title: str, timeout: float | None = 60, custom_id: str | None = None):
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


class BuildField[T](discord.ui.TextInput):
    def __init__(
        self,
        build: Build,
        attribute: str,
        attr_type: type[T],
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
    async def callback(self, interaction: Interaction[Any]) -> None:
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


class DynamicBuildEditButton[BotT: RedstoneSquid, V: discord.ui.View](
    discord.ui.DynamicItem[discord.ui.Button[V]], template=r"edit:build:(\d+)"
):
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
    async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]
        cls: type[Self], interaction: Interaction[BotT], item: Item[Any], match: re.Match[str], /
    ) -> Self:
        build = await Build.from_id(int(match.group(1)))
        assert build is not None
        return cls(build)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> Any:  # pyright: ignore [reportIncompatibleMethodOverride]
        async def _parent() -> ui.views.BuildInfoView[BotT]:
            # await self.build.reload()  # TODO: reload() is not implemented
            build = await self.build.get_persisted_copy()
            return ui.views.BuildInfoView(build)

        await ui.views.BuildEditView(self.build, parent=_parent).update(interaction)


class EphemeralBuildEditButton[BotT: RedstoneSquid, V: discord.ui.View](discord.ui.Button[V]):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(label="Edit", style=discord.ButtonStyle.secondary)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        await ui.views.BuildEditView(self.build).send(interaction, ephemeral=True)
