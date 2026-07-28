"""A cog with commands to editing builds."""

import asyncio
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, flag

from squid.bot import utils
from squid.bot.submission.ui.components import DynamicBuildEditButton
from squid.bot.submission.ui.views import BuildEditView, ConfirmationView
from squid.bot.utils import (
    MISSING,
    MissingType,
    RunningMessage,
    check_is_owner_server,
    check_is_trusted_or_staff,
    fix_converter_annotations,
)
from squid.bot.utils.converters import DimensionsConverter, GameTickConverter, ListConverter, NoneStrConverter
from squid.services.builds import BuildBusyError, BuildEditPatch, BuildNotFoundError

if TYPE_CHECKING:
    import squid.bot


class BuildEditCog[BotT: "squid.bot.RedstoneSquid"](Cog):
    """A cog with commands for editing builds."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.builds = bot.services.builds
        self.messages = bot.services.messages
        # https://github.com/Rapptz/discord.py/issues/7823#issuecomment-1086830458
        self.edit_ctx_menu = app_commands.ContextMenu(
            name="Edit Build",
            callback=self.edit_context_menu,
        )
        self.bot.tree.add_command(self.edit_ctx_menu)

    @commands.hybrid_group(name="edit")
    @check_is_trusted_or_staff()
    @check_is_owner_server()
    async def edit_group(self, ctx: Context[BotT]):
        """Edits a record in the database directly."""
        await ctx.send_help("edit")

    @fix_converter_annotations
    class EditDoorFlags(commands.FlagConverter):
        """Parameters information for the `/edit door` command."""

        def to_patch(self) -> BuildEditPatch:
            """Convert command flags without mutating a live build."""
            values = {
                "version_spec": self.works_in,
                "dimensions": self.build_size,
                "door_dimensions": self.door_size,
                "door_type": self.pattern,
                "door_orientation_type": self.door_type,
                "wiring_placement_restrictions": self.wiring_placement_restrictions,
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
        wiring_placement_restrictions: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: list[str] | MissingType = flag(default=MISSING, converter=ListConverter, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        extra_user_info: str | None | MissingType = flag(name="notes", converter=NoneStrConverter, default=MISSING, description='Any additional information about the build.')
        normal_closing_time: int | None | MissingType = flag(default=MISSING, converter=GameTickConverter, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int | None | MissingType = flag(default=MISSING, converter=GameTickConverter, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
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

    @edit_group.command(name="door")
    async def edit_door(self, ctx: Context[BotT], *, flags: EditDoorFlags):
        """Edits a door record in the database directly."""
        await ctx.defer()
        async with RunningMessage(ctx) as sent_message:
            try:
                async with self.builds.edit(flags.build_id, flags.to_patch()) as edit:
                    build = edit.build
                    if ctx.interaction:
                        await sent_message.edit(embed=utils.info_embed("Waiting", "User confirming changes..."))
                        view = ConfirmationView()
                        preview = await ctx.interaction.followup.send(
                            "Here is a preview of the changes. Use the buttons to confirm or cancel.",
                            embed=await self.bot.for_build(build).generate_embed(),
                            view=view,
                            ephemeral=True,
                            wait=True,
                        )
                        await view.wait()
                        await preview.delete()
                        if view.value is None:
                            await sent_message.edit(
                                embed=utils.info_embed("Timed out", "Build edit canceled due to inactivity.")
                            )
                            return
                        if view.value is False:
                            await sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user"))
                            return

                    await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                    await edit.commit()
            except BuildNotFoundError:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                await sent_message.edit(embed=error_embed)
                return
            except BuildBusyError:
                await sent_message.edit(
                    embed=utils.error_embed("Error", "Build is currently being edited by someone else.")
                )
                return

            await asyncio.gather(
                self.bot.for_build(build).update_messages(),
                sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully")),
            )
            return
        return

    async def edit_context_menu(self, interaction: discord.Interaction[BotT], message: discord.Message) -> None:
        """A context menu command to edit a build."""
        await interaction.response.defer(ephemeral=True)
        if message.author.id != self.bot.user.id:  # type: ignore
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        message_record = await self.messages.get(message.id)
        if message_record is None or message_record.build_id is None:
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        build = await self.builds.get(message_record.build_id)
        assert build is not None
        await BuildEditView(build, self.builds).send(interaction, ephemeral=True)
        return None


async def setup(bot: "squid.bot.RedstoneSquid") -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildEditCog(bot))
