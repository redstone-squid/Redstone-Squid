"""A cog with commands to editing builds."""

import asyncio
from typing import TYPE_CHECKING, Literal, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, flag

from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ui.views import BuildEditView, ConfirmationView
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.components import edit_layout, info_layout, no_mentions, text_layout
from squid.bot.utils.converters import (
    DimensionsConverter,
    GameTickConverter,
    ListConverter,
    NoneStrConverter,
    fix_converter_annotations,
)
from squid.bot.utils.embeds import RunningMessage
from squid.bot.utils.permissions import requires
from squid.bot.utils.sentinel import MISSING, MissingType
from squid.builds.application import BuildEditPatch, BuildService
from squid.core.i18n import _
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT

if TYPE_CHECKING:
    import squid.bot.app


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

    @fix_converter_annotations
    class EditDoorFlags(commands.FlagConverter):
        """Parameters for the `/build edit` command."""

        def to_patch(self) -> BuildEditPatch:
            """Convert command flags without mutating a live build."""
            values = {
                "version_spec": self.works_in,
                "dimensions": self.build_size,
                "door_dimensions": self.door_size,
                "door_type": self.pattern,
                "door_orientation_type": self.door_type,
                "wiring_placement_restrictions": self.wiring_placement_restrictions,
                "animated_restrictions": self.animated_restrictions,
                "component_restrictions": self.component_restrictions,
                "locationality": self.locationality,
                "directionality": self.directionality,
                "normal_closing_time": self.normal_closing_time,
                "normal_opening_time": self.normal_opening_time,
                "extra_user_info": self.extra_user_info,
                "creators_ign": self.creators,
                "image_urls": self.image_urls,
                "video_urls": self.video_urls,
                "world_download_urls": self.world_download_urls,
                "server_ip": self.server_ip,
                "coordinates": self.coordinates,
                "command_to_get_to_build": self.command_to_get_to_build,
                "completion_time": self.date_of_creation,
            }
            return BuildEditPatch.from_attributes({key: value for key, value in values.items() if value is not MISSING})

        # fmt: off
        build_id: int = flag(description='The ID of the submission.')
        door_size: tuple[int | None, int | None, int | None] | MissingType = flag(default=MISSING, converter=DimensionsConverter, description='e.g. *2x2* piston door. In width x height (x depth).')
        pattern: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] | MissingType = flag(default=MISSING, converter=NoneStrConverter(choices=["Door", "Skydoor", "Trapdoor"]), description='Door, Skydoor, or Trapdoor.')
        build_size: tuple[int | None, int | None, int | None] | MissingType = flag(default=MISSING, converter=DimensionsConverter, description='The dimension of the build. In width x height x depth.')
        works_in: str | None | MissingType = flag(default=MISSING, converter=NoneStrConverter, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        wiring_placement_restrictions: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='For example, "Seamless, Full Flush". See `/info docs` for the complete list.')
        animated_restrictions: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='For example, "Symmetrical, Full Sync". See `/info docs` for the complete list.')
        component_restrictions: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='For example, "No Pistons, No Slime Blocks". See `/info docs` for the complete list.')
        extra_user_info: str | None | MissingType = flag(name="notes", converter=NoneStrConverter, default=MISSING, description='Any additional information about the build.')
        normal_closing_time: int | None | MissingType = flag(default=MISSING, converter=GameTickConverter, description='The time it takes to close the door, in game ticks (20 per second).')
        normal_opening_time: int | None | MissingType = flag(default=MISSING, converter=GameTickConverter, description='The time it takes to open the door, in game ticks (20 per second).')
        date_of_creation: str | None | MissingType = flag(default=MISSING, converter=NoneStrConverter, description='The date the build was created.')
        creators: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes", "Not locational"] | MissingType = flag(default=MISSING, converter=NoneStrConverter(choices=["Locational", "Locational with fixes", "Not locational"]), description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes", "Not directional"] | MissingType = flag(default=MISSING, converter=NoneStrConverter(choices=["Directional", "Directional with fixes", "Not directional"]), description='Whether the build works in all directions, or only in certain directions.')
        image_urls: list[str] | MissingType = flag(name="image_links", default=MISSING, converter=ListConverter, description='Links to images of the build.')
        video_urls: list[str] | MissingType = flag(name="video_links", default=MISSING, converter=ListConverter, description='Links to videos of the build.')
        world_download_urls: list[str] | MissingType = flag(name="world_download_links", default=MISSING, converter=ListConverter, description='Links to download the world.')
        server_ip: str | None | MissingType = flag(default=MISSING, converter=NoneStrConverter, description='The IP of the server where the build is located.')
        coordinates: str | None | MissingType = flag(default=MISSING, converter=NoneStrConverter, description='The coordinates of the build in the server.')
        command_to_get_to_build: str | None | MissingType = flag(default=MISSING, converter=NoneStrConverter, description='The command to get to the build in the server.')
        # fmt: on

    @autocompletes(
        build_id="builds",
        pattern=suggests("approved_patterns", multi=True),
        works_in="approved_source_versions",
        wiring_placement_restrictions=suggests("approved_restrictions", multi=True),
        animated_restrictions=suggests("approved_restrictions", multi=True),
        component_restrictions=suggests("approved_restrictions", multi=True),
        creators=suggests("creators", multi=True),
    )
    @BuildCommandGroup.build_hybrid_group.command(name="edit")  # type: ignore
    @requires(BUILD_SUBMISSION_EDIT)
    async def edit_door(self, ctx: Context[BotT], *, flags: EditDoorFlags):
        """Edit a build with the full field list."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with RunningMessage(ctx, locale=locale) as sent_message:
            async with self.builds.edit(flags.build_id, flags.to_patch()) as edit:
                build = edit.build
                if ctx.interaction:
                    interaction = cast(discord.Interaction[discord.Client], ctx.interaction)
                    await edit_layout(
                        sent_message,
                        info_layout(t(locale, _("Waiting")), t(locale, _("User confirming changes..."))),
                        allowed_mentions=no_mentions(),
                    )
                    view = ConfirmationView(
                        t(locale, _("Here is a preview of the changes. Use the buttons to confirm or cancel.")),
                        locale=locale,
                    )
                    controls = view.actions
                    view.clear_items()
                    view.add_item(discord.ui.TextDisplay(t(locale, _("Review the proposed build changes."))))
                    view.add_item(await self.bot.for_build(build).render_container())
                    view.add_item(controls)
                    preview = await interaction.followup.send(
                        view=view,
                        ephemeral=True,
                        wait=True,
                        allowed_mentions=no_mentions(),
                    )
                    await view.wait()
                    await preview.delete()
                    if view.value is None:
                        await edit_layout(
                            sent_message,
                            info_layout(
                                t(locale, _("Timed out")),
                                t(locale, _("Build edit canceled due to inactivity.")),
                            ),
                            allowed_mentions=no_mentions(),
                        )
                        return
                    if view.value is False:
                        await edit_layout(
                            sent_message,
                            info_layout(t(locale, _("Cancelled")), t(locale, _("Build edit canceled by user"))),
                            allowed_mentions=no_mentions(),
                        )
                        return

                await edit_layout(
                    sent_message,
                    info_layout(t(locale, _("Editing")), t(locale, _("Editing build..."))),
                    allowed_mentions=no_mentions(),
                )
                await edit.commit()

            await asyncio.gather(
                self.bot.refresh_posts("build", str(build.id)),
                edit_layout(
                    sent_message,
                    info_layout(t(locale, _("Success")), t(locale, _("Build edited successfully"))),
                    allowed_mentions=no_mentions(),
                ),
            )
            return
        return

    async def edit_context_menu(self, interaction: discord.Interaction[BotT], message: discord.Message) -> None:
        """A context menu command to edit a build."""
        await interaction.response.defer(ephemeral=True)
        locale = await resolve_locale(interaction, self.bot.services.settings)
        if message.author.id != self.bot.user.id:  # type: ignore
            return await interaction.followup.send(
                view=text_layout(t(locale, _("This does not look like a build."))),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

        # Which build a card shows is a property of the post, not of the message: the
        # same message row is just a fact about a Discord message.
        post = await self.bot.services.posts.resolve(message.id)
        if post is None or post.resource_kind != "build":
            return await interaction.followup.send(
                view=text_layout(t(locale, _("This does not look like a build."))),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

        build = await self.builds.get(int(post.resource_key))
        if build is None:
            return await interaction.followup.send(
                view=text_layout(t(locale, _("This does not look like a build."))),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
        await BuildEditView(build, self.builds).send(interaction, ephemeral=True)
        return None
