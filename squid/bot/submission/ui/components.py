"""Selects and buttons for discord interactions."""

import logging
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Self, cast, override

import discord
from beartype.door import is_bearable
from discord import Interaction, TextStyle
from discord.ui import Item

from squid.bot.i18n import t
from squid.bot.submission.parse import get_formatter_and_parser_for_type
from squid.builds.domain import DOOR_ORIENTATION_NAMES, Build
from squid.core.i18n import _

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    import discord.types.interactions

    import squid.bot.app


logger = logging.getLogger(__name__)


class DoorTypeSelect(discord.ui.Select):
    def __init__(self, build: Build, *, locale: str | None = None) -> None:
        self.build = build
        self.locale = locale
        options = [
            discord.SelectOption(
                label=t(locale, _(door_type)),
                value=door_type,
                default=build.door_orientation_type == door_type,
            )
            for door_type in DOOR_ORIENTATION_NAMES
        ]
        super().__init__(
            placeholder=t(locale, _("Choose the door type (required)")),
            min_values=1,
            max_values=1,
            options=options,
        )

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        data = cast("discord.types.interactions.SelectMessageComponentInteractionData", interaction.data)
        self.build.door_orientation_type = data["values"][0]  # type: ignore
        parent = self.view
        if parent is not None and hasattr(parent, "refresh"):
            await parent.refresh(interaction)  # type: ignore[reportUnknownMemberType]
        else:
            await interaction.response.defer()


class DirectonalityLocationalitySelect(discord.ui.Select):
    def __init__(self, build: Build, *, locale: str | None = None) -> None:
        self.build = build
        self.locale = locale
        options = [
            discord.SelectOption(
                label=t(locale, _("Directional")),
                value="Directional",
                description=t(locale, _("May depend on the direction it faces")),
                default="Directional" in build.miscellaneous_restrictions,
            ),
            discord.SelectOption(
                label=t(locale, _("Locational")),
                value="Locational",
                description=t(locale, _("May depend on its position in the world")),
                default="Locational" in build.miscellaneous_restrictions,
            ),
        ]
        super().__init__(
            placeholder=t(locale, _("Optional location and direction limits")),
            min_values=0,
            max_values=2,
            options=options,
        )

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        data = cast("discord.types.interactions.SelectMessageComponentInteractionData", interaction.data)
        self.build.miscellaneous_restrictions = data["values"]
        parent = self.view
        if parent is not None and hasattr(parent, "refresh"):
            await parent.refresh(interaction)  # type: ignore[reportUnknownMemberType]
        else:
            await interaction.response.defer()


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
        except AttributeError as err:
            msg = f"Invalid attribute {attribute}"
            raise ValueError(msg) from err
        if not is_bearable(value, attr_type):  # type: ignore[arg-type]
            logger.error("Invalid hint for %s: %s", attribute, attr_type)

        if required is None:
            required = not is_bearable(None, attr_type)  # type: ignore[arg-type]

        if value is None:
            string_value = ""
        else:
            string_value = formatter(value)

        self.actual_value = value
        self.original_string_value = string_value
        self.current_string_value = string_value
        self.modified = False
        self.validation_error: str | None = None
        self.build = build
        self.attribute = attribute
        self.attr_type = attr_type
        self.parser = parser
        self.formatter = formatter
        self.display_label = label or attribute.replace("_", " ").title()
        super().__init__(
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
        """Parse and retain the proposed value without mutating the build."""
        self.validation_error = None
        if self.value == self.current_string_value:
            return

        self.modified = True
        self.default = self.value
        self.current_string_value = self.value
        try:
            value = self.parser(self.value)
        except Exception as error:
            self.modified = False
            self.validation_error = str(error) or t(None, _("Invalid value"))
            return

        self.actual_value = value

    @property
    def summary(self) -> str:
        return f"{self.display_label}: {self.value}"

    def to_label(self) -> discord.ui.Label:
        """Wrap this input in the accessible V2 modal layout component."""
        return discord.ui.Label(text=self.display_label, component=self)


def get_text_input[T](build: Build, attribute: str, attr_type: type[T] | None = None, **kwargs: Any) -> BuildField[T]:
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
    formatter, parser = get_formatter_and_parser_for_type(attr_type)
    return BuildField(build, attribute, attr_type, formatter, parser, **kwargs)


class DynamicBuildEditButton[
    BotT: "squid.bot.app.RedstoneSquid",
    V: discord.ui.LayoutView,
](discord.ui.DynamicItem[discord.ui.Button[V]], template=r"edit:build:(\d+)"):
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
    async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        cls: type[Self], interaction: Interaction[BotT], item: Item[Any], match: re.Match[str], /
    ) -> Self:
        build = await interaction.client.services.builds.get(int(match.group(1)))
        assert build is not None
        return cls(build)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> Any:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        # FIXME: circular import
        from squid.bot.submission.ui.views import BuildEditView

        await BuildEditView(self.build, interaction.client.services.builds).send(interaction)


class EphemeralBuildEditButton[
    BotT: "squid.bot.app.RedstoneSquid",
    V: discord.ui.LayoutView,
](discord.ui.Button[V]):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(label="Edit", style=discord.ButtonStyle.secondary)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        from squid.bot.submission.ui.views import BuildEditView

        await BuildEditView(self.build, interaction.client.services.builds).send(interaction, ephemeral=True)
