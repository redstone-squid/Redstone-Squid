"""Build editing commands."""

from typing import TYPE_CHECKING, Self

import discord

import squid_ui_discord as sd
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ui.opening import open_build_editor, prepare_build_editor, show_build_editor
from squid.bot.ui import error_node, text_node
from squid.builds.application import BuildService
from squid.builds.domain import DoorOrientationLiteral
from squid.core.i18n import tr
from squid.messages.application import MessageService

if TYPE_CHECKING:
    import squid.bot.app


def _split_list(value: str) -> list[str]:
    """Split a comma-separated option value, dropping empty entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


class BuildEditCommands[BotT: "squid.bot.app.RedstoneSquid"](BuildCommandGroup[BotT]):
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
        """Stage the given options into a build editor and open it for the rest.

        Not registered as a slash command; an option the build has no field for is refused rather than dropped.
        """
        request = await self.ui.request(interaction)
        await request.defer("private")

        build = await self.builds.get(build_id)
        if build is None:
            await request.respond(error_node(tr("Error"), tr("No build with that ID.")))
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
            # One option fills all four buckets, as `/build submit` does: which bucket a restriction
            # belongs in is a fact about the restriction, not the editor's decision.
            buckets = await self.builds.sort_restrictions(_split_list(restrictions))
            staged["wiring_placement_restrictions"] = ", ".join(buckets["wiring-placement"])
            staged["animated_restrictions"] = ", ".join(buckets["animated"])
            staged["component_restrictions"] = ", ".join(buckets["component"])
            staged["miscellaneous_restrictions"] = ", ".join(buckets["miscellaneous"])

        inapplicable = [attribute for attribute, value in staged.items() if not screen.stage(attribute, value)]
        if inapplicable:
            await request.respond(
                error_node(
                    tr("Not a field of this build"),
                    tr(
                        "This build has no {fields}. Open the workspace to see what it does have.",
                        fields=", ".join(sorted(inapplicable)),
                    ),
                )
            )
            return

        await show_build_editor(request, screen)

    @sd.context_menu(name="Edit Build", defer="private")
    async def edit_context_menu(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        if message.author.id != self.bot.user.id:  # type: ignore
            return text_node(tr("This does not look like a build."))

        # Which build a card shows is a property of the post, not of the message row.
        post = await self.bot.services.posts.resolve(message.id)
        if post is None or post.resource_kind != "build":
            return text_node(tr("This does not look like a build."))

        build = await self.builds.get(int(post.resource_key))
        if build is None:
            return text_node(tr("This does not look like a build."))
        await open_build_editor(request, build)
        return None
