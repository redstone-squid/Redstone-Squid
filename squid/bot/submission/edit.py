"""A cog with commands to editing builds."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands

import squid_ui_discord as sd
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ui.opening import open_build_editor, prepare_build_editor, show_build_editor
from squid.bot.ui import error_node, text_node
from squid.builds.application import BuildService
from squid.builds.domain import DoorOrientationLiteral
from squid.core.i18n import _
from squid.messages.application import MessageService

if TYPE_CHECKING:
    import squid.bot.app


def _split_list(value: str) -> list[str]:
    """Split a comma-separated option value, dropping empty entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


class BuildEditCommands[BotT: "squid.bot.app.RedstoneSquid"](BuildCommandGroup[BotT]):
    """A cog with commands for editing builds."""

    bot: BotT
    builds: BuildService
    messages: MessageService

    def register_edit_context_menu(self) -> None:
        """Register the build edit context menu."""
        # https://github.com/Rapptz/discord.py/issues/7823#issuecomment-1086830458
        self.edit_ctx_menu = app_commands.ContextMenu(
            name="Edit Build",
            callback=self.edit_context_menu,
        )
        self.bot.tree.add_command(self.edit_ctx_menu)

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
        """Edit a build. Whatever you fill in is staged; the workspace opens for the rest."""
        await interaction.response.defer(ephemeral=True)
        invocation = await sd.Invocation.of(interaction)
        locale = await resolve_locale(interaction, self.bot.services.settings)
        build = await self.builds.get(build_id)
        if build is None:
            await invocation.reply(
                error_node(t(locale, _("Error")), t(locale, _("No build with that ID."))),
                visibility="personal",
            )
            return

        screen = await prepare_build_editor(interaction, build, self.builds)
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
            # One option replaces all four buckets, the same way `/build submit` reads one:
            # which bucket a restriction belongs in is a fact about the restriction, not a
            # decision the person editing should have to make.
            buckets = await self.builds.sort_restrictions(_split_list(restrictions))
            staged["wiring_placement_restrictions"] = ", ".join(buckets["wiring-placement"])
            staged["animated_restrictions"] = ", ".join(buckets["animated"])
            staged["component_restrictions"] = ", ".join(buckets["component"])
            staged["miscellaneous_restrictions"] = ", ".join(buckets["miscellaneous"])

        inapplicable = [attribute for attribute, value in staged.items() if not screen.stage(attribute, value)]
        if inapplicable:
            # Dropping a typed option silently is the failure mode this command was merged to
            # end, so a door option on a build with no door is a refusal rather than a no-op.
            await invocation.reply(
                error_node(
                    t(locale, _("Not a field of this build")),
                    t(
                        locale,
                        _("This build has no {fields}. Open the workspace to see what it does have."),
                        fields=", ".join(sorted(inapplicable)),
                    ),
                ),
                visibility="personal",
            )
            return

        await show_build_editor(interaction, screen)

    async def edit_context_menu(self, interaction: discord.Interaction[BotT], message: discord.Message) -> None:
        """A context menu command to edit a build."""
        await interaction.response.defer(ephemeral=True)
        invocation = await sd.Invocation.of(interaction)
        locale = await resolve_locale(interaction, self.bot.services.settings)
        if message.author.id != self.bot.user.id:  # type: ignore
            await invocation.reply(text_node(t(locale, _("This does not look like a build."))), visibility="personal")
            return

        # Which build a card shows is a property of the post, not of the message: the
        # same message row is just a fact about a Discord message.
        post = await self.bot.services.posts.resolve(message.id)
        if post is None or post.resource_kind != "build":
            await invocation.reply(text_node(t(locale, _("This does not look like a build."))), visibility="personal")
            return

        build = await self.builds.get(int(post.resource_key))
        if build is None:
            await invocation.reply(text_node(t(locale, _("This does not look like a build."))), visibility="personal")
            return
        await open_build_editor(interaction, build)
