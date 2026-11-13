"""Open build editors from Discord messages and optional prefilled values."""

from collections.abc import Mapping
from typing import TYPE_CHECKING

import discord

from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.input import split_values
from squid.bot.submission.parse import parse_dimensions
from squid.bot.submission.ui.build_edit import BuildEditWorkspace
from squid.bot.submission.ui.opening import prepare_build_editor, show_build_editor
from squid.bot.ui import error_node
from squid.builds.application import BuildService
from squid.builds.domain import DoorOrientationLiteral
from squid.core.errors import JSONValue
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.submissions.domain import match_choice
from squid_ui.text import TextLike

if TYPE_CHECKING:
    import squid.bot.app


class BuildEditCommands[BotT: squid.bot.app.RedstoneSquid](BuildCommandGroup[BotT]):
    """A cog with commands for editing builds."""

    bot: BotT
    builds: BuildService
    messages: MessageService

    async def edit_build(
        self,
        interaction: discord.Interaction[BotT],
        build_id: int,
        *,
        door_size: str | None = None,
        door_type: DoorOrientationLiteral | None = None,
        pattern: str | None = None,
        build_size: str | None = None,
        versions: str | None = None,
        restrictions: str | None = None,
        creators: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Open an editor with the supplied values already staged; this helper is not a registered command.

        Restrictions and patterns are names from the catalogue and replace the build's current
        ones. Options that do not apply to the build, or values the form rejects, are refused
        with an error instead of opening the editor. Nothing is saved until the user reviews it.
        """
        request = await self.ui.request(interaction)
        await request.defer("private")

        build = await self.builds.get(build_id)
        if build is None:
            await request.respond(
                error_node(
                    tr(t"bot-submission-edit-error = Error"),
                    tr(t"bot-submission-edit-no-build-with-that-id = No build with that ID."),
                ),
                audience="personal",
            )
            return

        screen = await prepare_build_editor(request, build, self.builds)
        problem = _prefill(
            screen.workspace,
            door_size=door_size,
            door_type=door_type,
            pattern=pattern,
            build_size=build_size,
            versions=versions,
            restrictions=restrictions,
            creators=creators,
            notes=notes,
        )
        if problem is not None:
            await request.respond(
                error_node(tr(t"bot-submission-edit-cannot-use-options = Cannot use these options"), problem),
                audience="personal",
            )
            return

        await show_build_editor(request, screen)


def _prefill(
    workspace: BuildEditWorkspace,
    *,
    door_size: str | None,
    door_type: str | None,
    pattern: str | None,
    build_size: str | None,
    versions: str | None,
    restrictions: str | None,
    creators: str | None,
    notes: str | None,
) -> TextLike | None:
    """Stage the command's options as edit-form answers, or return why they cannot be used."""
    values: dict[str, JSONValue] = {}
    # Field ID -> the command option that supplied it, so errors can name the option.
    options: dict[str, str] = {}

    def put(option: str, field_values: Mapping[str, JSONValue]) -> None:
        values.update(field_values)
        options.update(dict.fromkeys(field_values, option))

    try:
        if door_size is not None:
            put(
                "door_size",
                dict(
                    zip(("opening_width", "opening_height", "opening_depth"), parse_dimensions(door_size), strict=True)
                ),
            )
        if build_size is not None:
            put(
                "build_size",
                dict(
                    zip(("capture_width", "capture_height", "capture_depth"), parse_dimensions(build_size), strict=True)
                ),
            )
    except ValueError:
        return tr(
            t"bot-submission-edit-size-format = Enter sizes as width x height x depth, such as 5x7x4. "
            t"Use ? for a value you don't know."
        )
    if door_type is not None:
        put("door_type", {"door_orientation": door_type.lower()})
    if versions is not None:
        put("versions", {"version_compatibility": versions.strip() or None})
    if creators is not None:
        put("creators", {"creators": split_values(creators)})
    if notes is not None:
        put("notes", {"description": notes.strip() or None})
    unknown: list[str] = []
    for option, field_id, text, source in (
        ("pattern", "patterns", pattern, "approved_patterns"),
        ("restrictions", "restrictions", restrictions, "approved_restrictions"),
    ):
        if text is None:
            continue
        choices = workspace.option_sets.get(source, ())
        keys = [match_choice(name, choices) for name in split_values(text)]
        unknown.extend(name for name, key in zip(split_values(text), keys, strict=True) if key is None)
        put(option, {field_id: [key for key in keys if key is not None]})

    visible = {field.id for field in workspace.fields}
    inapplicable = sorted({option for field_id, option in options.items() if field_id not in visible})
    if inapplicable:
        names = ", ".join(inapplicable)
        return tr(
            t"build-editor-fields-unavailable = This build has no {names}. Open the workspace to see what it does have."
        )
    if unknown:
        names = ", ".join(unknown)
        return tr(
            t"bot-submission-edit-unknown-names = Not in the catalogue: {names}. "
            t"Choose from the catalogue, or propose a new tag."
        )
    return workspace.prefill(values)
