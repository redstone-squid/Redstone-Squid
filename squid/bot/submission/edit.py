"""Open build editors from Discord messages and optional prefilled values."""

from typing import TYPE_CHECKING

import discord

from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.input import split_values
from squid.bot.submission.ui.opening import prepare_build_editor, show_build_editor
from squid.bot.ui import error_node
from squid.builds.application import BuildService
from squid.builds.domain import DoorOrientationLiteral
from squid.core.i18n import tr
from squid.messages.application import MessageService

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
        """Stage supplied values and open an editor; this helper is not a registered command.

        A supplied restriction list replaces every restriction category. Reject fields
        that do not apply to the build, leaving persistence to the editor's review step.
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
        staged: dict[str, str] = {
            attribute: value
            for attribute, value in (
                ("door_dimensions", door_size),
                ("door_orientation_type", door_type),
                ("door_type", pattern),
                ("dimensions", build_size),
                ("version_spec", versions),
                ("creators_ign", creators),
                ("extra_user_info", notes),
            )
            if value is not None
        }
        if restrictions is not None:
            # Use taxonomy categories so the caller can supply one combined restriction list.
            buckets = await self.builds.sort_restrictions(split_values(restrictions))
            staged["wiring_placement_restrictions"] = ", ".join(buckets["wiring-placement"])
            staged["animated_restrictions"] = ", ".join(buckets["animated"])
            staged["component_restrictions"] = ", ".join(buckets["component"])
            staged["miscellaneous_restrictions"] = ", ".join(buckets["miscellaneous"])

        inapplicable = [attribute for attribute, value in staged.items() if not screen.stage(attribute, value)]
        if inapplicable:
            fields = ", ".join(sorted(inapplicable))
            await request.respond(
                error_node(
                    tr(t"bot-submission-edit-not-a-field-of-this-build = Not a field of this build"),
                    tr(
                        t"build-editor-fields-unavailable = This build has no {fields}"
                        t". Open the workspace to see what it does have."
                    ),
                ),
                audience="personal",
            )
            return

        await show_build_editor(request, screen)
