"""Models, view and buttons for the submission process."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Callable, Self, cast, override

import discord
from beartype.door import is_bearable
from discord import Interaction, TextStyle
from discord.ui import Item

from squid.bot.submission import ui
from squid.bot.submission.parse import get_formatter_and_parser_for_type, parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.views import BuildEditView
from squid.db.builds import Build
from squid.db.schema import DOOR_ORIENTATION_NAMES, RECORD_CATEGORIES

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    from discord.types.interactions import SelectMessageComponentInteractionData

    from squid.bot.main import RedstoneSquid


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


class EditModal[BotT: RedstoneSquid](discord.ui.Modal):
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


class BuildField[T](discord.ui.TextInput):
    """A text input field for editing a build attribute, that is tied to a Build object."""

    def __init__(
        self,
        build: Build,
        attribute: str,
        attr_type: type[T],
        formatter: Callable[[T], str],
        parser: Callable[[str], T],
        *,
        label: str | None = None,
        style: TextStyle = TextStyle.short,
        custom_id: str | None = None,
        placeholder: str | None = None,
        required: bool | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        row: int | None = None,
    ):
        """Initializes a BuildField.

        Args:
            build: The build object to edit.
            attribute: The attribute of the build object to edit.
            attr_type: The type of the attribute.
            formatter: A function to format the attribute value as a string.
            parser: A function to parse the string value into the attribute type. If None, the attribute type can only be str.
            label: The label of the field. Defaults to the attribute name prettified.
            style: The style of the field.
            custom_id: The custom ID of the field.
            placeholder: The placeholder of the field.
            required: Whether the field is required. If None, it is inferred from the attribute type hint.
            min_length: The minimum length of the field.
            max_length: The maximum length of the field.
            row: The row of the field.
        """
        try:
            value: T = getattr(build, attribute)
        except AttributeError:
            raise ValueError(f"Invalid attribute {attribute}")
        if not is_bearable(value, attr_type):
            logging.error(f"Invalid hint for {attribute}: {attr_type}")

        if required is None:
            required = is_bearable(None, attr_type)

        if value is None:
            string_value = ""
        else:
            string_value = formatter(value)

        self.actual_value = value
        self.original_string_value = string_value
        self.current_string_value = string_value
        self.modified = False
        self.build = build
        self.attribute = attribute
        self.attr_type = attr_type
        self.parser = parser
        self.formatter = formatter
        super().__init__(
            label=label or attribute.replace("_", " ").title(),
            style=style,
            custom_id=os.urandom(16).hex() if custom_id is None else custom_id,
            placeholder=placeholder,
            default=string_value,
            required=required,
            min_length=min_length,
            max_length=max_length,
            row=row,
        )

    async def on_modal_submit(self) -> None:
        # If the value hasn't changed, don't bother trying to set it
        if self.value == self.current_string_value:
            return

        self.modified = True
        self.default = self.value
        self.current_string_value = self.value
        try:
            value = self.parser(self.value)
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


def get_text_input[T](build: Build, attribute: str, attr_type: type[T] | None = None, **kwargs) -> BuildField[T]:
    """
    Gets the bound input for the attribute.

    Args:
        build: The build object to get the input for.
        attribute: The attribute to get the input for.
        attr_type: The type of the attribute. If not provided, it will be inferred from the attribute.
        **kwargs: Additional keyword arguments to pass to the BuildField constructor.
    """
    if attr_type is None:
        attr_type = build.get_attr_type(attribute)
    attr_type = cast(type[T], attr_type)
    formatter, parser = get_formatter_and_parser_for_type(attr_type)
    return BuildField(build, attribute, attr_type, formatter, parser, **kwargs)


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

        await BuildEditView(self.build, parent=_parent).update(interaction)


class EphemeralBuildEditButton[BotT: RedstoneSquid, V: discord.ui.View](discord.ui.Button[V]):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(label="Edit", style=discord.ButtonStyle.secondary)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        await BuildEditView(self.build).send(interaction, ephemeral=True)
