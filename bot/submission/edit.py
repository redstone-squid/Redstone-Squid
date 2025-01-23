"""A cog with commands to editing builds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, flag
from postgrest.base_request_builder import SingleAPIResponse

from bot.submission.parse import parse_dimensions
from bot import utils
from bot.submission.ui import ConfirmationView, DynamicBuildEditButton, EditView
from bot.utils import RunningMessage, check_is_trusted_or_staff, fix_converter_annotations, check_is_owner_server
from database.builds import Build

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


class BuildEditCog(Cog):
    """A cog with commands for editing builds."""

    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        # https://github.com/Rapptz/discord.py/issues/7823#issuecomment-1086830458
        self.edit_ctx_menu = app_commands.ContextMenu(
            name="Edit Build",
            callback=self.edit_context_menu,
        )
        self.bot.tree.add_command(self.edit_ctx_menu)

    @commands.hybrid_group(name="edit")
    @check_is_trusted_or_staff()
    @commands.check(check_is_owner_server)
    async def edit_group(self, ctx: Context):
        """Edits a record in the database directly."""
        await ctx.send_help("edit")

    @fix_converter_annotations
    class EditDoorFlags(commands.FlagConverter):
        """Parameters information for the `/edit door` command."""

        async def to_build(self) -> Build | None:
            """Convert the flags to a build object, returns None if the build_id is invalid."""
            build = await Build.from_id(self.build_id)
            if build is None:
                return None

            # FIXME: need to distinguish between None and removing the value
            if (works_in := self.works_in) is not None:
                build.version_spec = works_in
            if (build_size := self.build_size) is not None:
                build_dimensions = parse_dimensions(build_size)
                build.width, build.height, build.depth = build_dimensions
            if (door_size := self.door_size) is not None:
                door_dimensions = parse_dimensions(door_size)
                build.door_width, build.door_height, build.door_depth = door_dimensions
            if (pattern := self.pattern) is not None:
                build.door_type = pattern.split(", ")
            if (door_type := self.door_type) is not None:
                build.door_orientation_type = door_type
            if (wp_res := self.wiring_placement_restrictions) is not None:
                build.wiring_placement_restrictions = wp_res.split(", ")
            if (co_res := self.component_restrictions) is not None:
                build.component_restrictions = co_res.split(", ")
            misc_restrictions = [self.locationality, self.directionality]
            build.miscellaneous_restrictions = [x for x in misc_restrictions if x is not None]
            if self.normal_closing_time is not None:
                build.normal_closing_time = self.normal_closing_time
            if self.normal_opening_time is not None:
                build.normal_opening_time = self.normal_opening_time
            if self.information_about_build is not None:
                build.information["user"] = self.information_about_build
            if (ign := self.in_game_name_of_creator) is not None:
                build.creators_ign = ign.split(", ")
            if self.link_to_image is not None:
                build.image_urls = [self.link_to_image]
            if self.link_to_youtube_video is not None:
                build.video_urls = [self.link_to_youtube_video]
            if self.link_to_world_download is not None:
                build.world_download_urls = [self.link_to_world_download]
            if self.server_ip is not None:
                build.server_info["server_ip"] = self.server_ip
            if self.coordinates is not None:
                build.server_info["coordinates"] = self.coordinates
            if self.command_to_get_to_build is not None:
                build.server_info["command_to_build"] = self.command_to_get_to_build
            if self.date_of_creation is not None:
                build.completion_time = self.date_of_creation
            return build

        # fmt: off
        build_id: int = flag(description='The ID of the submission.')
        door_size: str | None = flag(default=None, description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        pattern: str | None = flag(default=None, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] | None = flag(default=None, description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        wiring_placement_restrictions: str | None = flag(default=None, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: str | None = flag(default=None, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        information_about_build: str | None = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int | None = flag(default=None, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int | None = flag(default=None, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
        date_of_creation: str | None = flag(default=None, description='The date the build was created.')
        in_game_name_of_creator: str | None = flag(default=None, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes"] | None = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes"] | None = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        link_to_image: str | None = flag(default=None, description='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"')
        link_to_youtube_video: str | None = flag(default=None, description='A link to a video of the build.')
        link_to_world_download: str | None = flag(default=None, description='A link to download the world.')
        server_ip: str | None = flag(default=None, description='The IP of the server where the build is located.')
        coordinates: str | None = flag(default=None, description='The coordinates of the build in the server.')
        command_to_get_to_build: str | None = flag(default=None, description='The command to get to the build in the server.')
        # fmt: on

    @edit_group.command(name="door")
    async def edit_door(self, ctx: Context, *, flags: EditDoorFlags):
        """Edits a door record in the database directly."""
        await ctx.defer()
        async with RunningMessage(ctx) as sent_message:
            build = await flags.to_build()
            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                return await sent_message.edit(embed=error_embed)

            preview_embed = await build.generate_embed()

            # Show a preview of the changes and ask for confirmation
            await sent_message.edit(embed=utils.info_embed("Waiting", "User confirming changes..."))
            if ctx.interaction:
                view = ConfirmationView()
                preview = await ctx.interaction.followup.send(
                    "Here is a preview of the changes. Use the buttons to confirm or cancel.",
                    embed=preview_embed,
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
                elif view.value:
                    await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                    await build.save()
                    await build.update_messages(self.bot)
                    await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))
                else:
                    await sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user"))
            else:  # Not an interaction, so we can't use buttons for confirmation
                await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                await build.save()
                await build.update_messages(self.bot)
                await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))

    async def edit_context_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """A context menu command to edit a build."""
        await interaction.response.defer(ephemeral=True)
        if message.author.id != self.bot.user.id:  # type: ignore
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        response: SingleAPIResponse[dict[str, int | None]] | None = await self.bot.db.table("messages").select("build_id").eq("message_id", message.id).maybe_single().execute()
        if response is None:
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)
        else:
            build_id = response.data["build_id"]

        if build_id is None:
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        build = await Build.from_id(build_id)
        assert build is not None
        await EditView(build).send(interaction, ephemeral=True)


async def setup(bot: RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildEditCog(bot))
