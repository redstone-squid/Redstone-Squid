"""Selects and buttons for discord interactions."""

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast, override

import discord
from beartype.door import is_bearable
from discord import Interaction, TextStyle

from squid.bot.i18n import resolve_locale, t
from squid.bot.routes import routes
from squid.bot.submission.parse import get_formatter_and_parser_for_type
from squid.bot.ui import respond_payload, text_layout
from squid.builds.domain import DOOR_ORIENTATION_NAMES, Build, BuildDraft, DoorBuild
from squid.core.i18n import _

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    import discord.types.interactions

    import squid.bot.app
    from squid.bot.app import RedstoneSquid


logger = logging.getLogger(__name__)

builds = routes.group("builds")
build_edit = builds.define("{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))


class DoorTypeSelect(discord.ui.Select):
    def __init__(self, draft: BuildDraft, *, locale: str | None = None) -> None:
        self.draft = draft
        self.locale = locale
        options = [
            discord.SelectOption(
                label=t(locale, _(door_type)),
                value=door_type,
                default=draft.door_orientation == door_type,
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
        self.draft.door_orientation = data["values"][0]  # type: ignore
        parent = self.view
        if parent is not None and hasattr(parent, "refresh"):
            await parent.refresh(interaction)  # type: ignore[reportUnknownMemberType]
        else:
            await interaction.response.defer()


class DirectonalityLocationalitySelect(discord.ui.Select):
    def __init__(self, draft: BuildDraft, *, locale: str | None = None) -> None:
        self.draft = draft
        self.locale = locale
        options = [
            discord.SelectOption(
                label=t(locale, _("Directional")),
                value="Directional",
                description=t(locale, _("May depend on the direction it faces")),
                default="Directional" in draft.miscellaneous_restrictions,
            ),
            discord.SelectOption(
                label=t(locale, _("Locational")),
                value="Locational",
                description=t(locale, _("May depend on its position in the world")),
                default="Locational" in draft.miscellaneous_restrictions,
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
        self.draft.miscellaneous_restrictions = data["values"]
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
        value: T = _read_edit_value(build, attribute)
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
        self.stage(self.value)

    def stage(self, text: str) -> None:
        """Take a proposed value from anywhere, not only from a submitted modal.

        `/build edit` fills fields from its typed options before the workspace is ever
        rendered, so the parse-and-remember half of a modal submission has to be reachable
        without one.
        """
        self.validation_error = None
        if text == self.current_string_value:
            return

        self.modified = True
        self.default = text
        self.current_string_value = text
        try:
            value = self.parser(text)
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


# The generic edit UI is keyed by BuildEditPatch field names, which no longer all
# match domain attributes: patterns and the door facts live on category
# subclasses, and the url views are read-only projections of `links`.
_EDIT_FIELD_READERS: dict[str, tuple[Callable[[Build], Any], Any]] = {
    "door_type": (lambda build: list(build.patterns), list[str]),
    "door_orientation_type": (
        lambda build: build.orientation if isinstance(build, DoorBuild) else None,
        str | None,
    ),
    "door_dimensions": (
        lambda build: build.door_dimensions if isinstance(build, DoorBuild) else None,
        tuple[int | None, int | None, int | None] | None,
    ),
    "normal_opening_time": (
        lambda build: build.normal_opening_time if isinstance(build, DoorBuild) else None,
        int | None,
    ),
    "normal_closing_time": (
        lambda build: build.normal_closing_time if isinstance(build, DoorBuild) else None,
        int | None,
    ),
    # These three are stored inside `extra_info["server_info"]` rather than as attributes, and
    # `extra_user_info` is written to both `description` and `extra_info["user"]` on apply.
    "extra_user_info": (lambda build: build.description, str | None),
    "server_ip": (lambda build: _server_info(build).get("server_ip"), str | None),
    "coordinates": (lambda build: _server_info(build).get("coordinates"), str | None),
    "command_to_get_to_build": (lambda build: _server_info(build).get("command_to_build"), str | None),
    "image_urls": (lambda build: list(build.image_urls), list[str]),
    "video_urls": (lambda build: list(build.video_urls), list[str]),
    "world_download_urls": (lambda build: list(build.world_download_urls), list[str]),
    "schematic_urls": (lambda build: list(build.schematic_urls), list[str]),
    "render_urls": (lambda build: list(build.render_urls), list[str]),
}


def _server_info(build: Build) -> dict[str, Any]:
    return dict(build.extra_info.get("server_info", {}))


def _read_edit_value(build: Build, attribute: str) -> Any:
    reader = _EDIT_FIELD_READERS.get(attribute)
    if reader is not None:
        return reader[0](build)
    try:
        return getattr(build, attribute)
    except AttributeError as err:
        msg = f"Invalid attribute {attribute}"
        raise ValueError(msg) from err


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
        reader = _EDIT_FIELD_READERS.get(attribute)
        attr_type = reader[1] if reader is not None else build.get_attr_type(attribute)
    formatter, parser = get_formatter_and_parser_for_type(attr_type)
    return BuildField(build, attribute, attr_type, formatter, parser, **kwargs)


@builds.route(build_edit)
async def edit_build(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    # FIXME: circular import
    from squid.bot.submission.ui.views import BuildEditComponent

    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        # The card outlived its build; say so rather than failing the interaction silently.
        locale = await resolve_locale(interaction, interaction.client.services.settings)
        await respond_payload(interaction, text_layout(t(locale, _("That build no longer exists."))))
        return
    await BuildEditComponent(build, interaction.client.services.builds).send(interaction)


class EphemeralBuildEditButton[
    BotT: "squid.bot.app.RedstoneSquid",
    V: discord.ui.LayoutView,
](discord.ui.Button[V]):
    def __init__(self, build: Build):
        self.build = build
        super().__init__(label="Edit", style=discord.ButtonStyle.secondary)

    @override
    async def callback(self, interaction: Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        from squid.bot.submission.ui.views import BuildEditComponent

        await BuildEditComponent(self.build, interaction.client.services.builds).send(interaction, ephemeral=True)
