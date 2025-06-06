"""A cog with commands to editing builds."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, flag
from postgrest.base_request_builder import SingleAPIResponse

from squid.bot import utils
from squid.bot.converter import DimensionsConverter, GameTickConverter, ListConverter, NoneStrConverter
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
from squid.db.builds import Build

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


class BuildEditCog[BotT: RedstoneSquid](Cog):
    """A cog with commands for editing builds."""

    def __init__(self, bot: BotT):
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
    async def edit_group(self, ctx: Context[BotT]):
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

            # Technically we can match both the variable names and the attribute names and use setattr
            # to set the values in a loop, but this explicitness helps with refactoring and readability.
            # FIXME: need to distinguish between None and removing the value
            if self.works_in is not MISSING:
                build.version_spec = self.works_in
            if self.build_size is not MISSING:
                build.width, build.height, build.depth = self.build_size
            if self.door_size is not MISSING:
                build.door_width, build.door_height, build.door_depth = self.door_size
            if self.pattern is not MISSING:
                build.door_type = self.pattern
            if self.door_type is not MISSING:
                build.door_orientation_type = self.door_type
            if self.wiring_placement_restrictions is not MISSING:
                build.wiring_placement_restrictions = self.wiring_placement_restrictions
            if self.component_restrictions is not MISSING:
                build.component_restrictions = self.component_restrictions
            if self.locationality is not MISSING:
                try:
                    build.miscellaneous_restrictions.remove("Locational")
                except ValueError:
                    pass
                try:
                    build.miscellaneous_restrictions.remove("Locational with fixes")
                except ValueError:
                    pass
                if self.locationality != "Not locational":
                    build.miscellaneous_restrictions.append(self.locationality)

            if self.directionality is not MISSING:
                try:
                    build.miscellaneous_restrictions.remove("Directional")
                except ValueError:
                    pass
                try:
                    build.miscellaneous_restrictions.remove("Directional with fixes")
                except ValueError:
                    pass
                if self.directionality != "Not directional":
                    build.miscellaneous_restrictions.append(self.directionality)

            if self.normal_closing_time is not MISSING:
                build.normal_closing_time = self.normal_closing_time
            if self.normal_opening_time is not MISSING:
                build.normal_opening_time = self.normal_opening_time
            if (user_info := self.extra_user_info) is not MISSING:
                if user_info is not None:
                    build.extra_info["user"] = user_info
                else:
                    build.extra_info.pop("user", None)
            if self.creators is not MISSING:
                build.creators_ign = self.creators
            if self.image_urls is not MISSING:
                build.image_urls = self.image_urls
            if self.video_urls is not MISSING:
                build.video_urls = self.video_urls
            if self.world_download_urls is not MISSING:
                build.world_download_urls = self.world_download_urls

            server_info = build.extra_info.get("server_info", {})
            if (server_ip := self.server_ip) is not MISSING:
                if server_ip is not None:
                    server_info["server_ip"] = server_ip
                else:
                    server_info.pop("server_ip", None)

            if (coordinates := self.coordinates) is not MISSING:
                if coordinates is not None:
                    server_info["coordinates"] = coordinates
                else:
                    server_info.pop("coordinates", None)

            if (command_to_get_to_build := self.command_to_get_to_build) is not MISSING:
                if command_to_get_to_build is not None:
                    server_info["command_to_build"] = command_to_get_to_build
                else:
                    server_info.pop("command_to_build", None)
            if server_info:
                build.extra_info["server_info"] = server_info

            if self.date_of_creation is not MISSING:
                build.completion_time = self.date_of_creation
            return build

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
            build = await flags.to_build()
            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                await sent_message.edit(embed=error_embed)
                return None

            if not await build.lock.acquire(blocking=False):
                await sent_message.edit(
                    embed=utils.error_embed("Error", "Build is currently being edited by someone else.")
                )
                return None

            # If in a slash command, we show a preview and ask for confirmation, otherwise we just edit the build
            if ctx.interaction:
                # Show a preview of the changes and ask for confirmation
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
                    await asyncio.gather(
                        build.lock.release(),
                        sent_message.edit(
                            embed=utils.info_embed("Timed out", "Build edit canceled due to inactivity.")
                        ),
                    )
                    return None
                elif view.value is False:
                    await asyncio.gather(
                        build.lock.release(),
                        sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user")),
                    )
                    return None

            await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
            await build.save()
            await build.lock.release()
            await self.bot.for_build(build).update_messages()
            await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))
            return None
        return None

    async def edit_context_menu(self, interaction: discord.Interaction[BotT], message: discord.Message) -> None:
        """A context menu command to edit a build."""
        await interaction.response.defer(ephemeral=True)
        if message.author.id != self.bot.user.id:  # type: ignore
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        response: SingleAPIResponse[dict[str, int | None]] | None = (
            await self.bot.db.table("messages").select("build_id").eq("id", message.id).maybe_single().execute()
        )
        if response is None:
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)
        else:
            build_id = response.data["build_id"]

        if build_id is None:
            return await interaction.followup.send("This does not look like a build.", ephemeral=True)

        build = await Build.from_id(build_id)
        assert build is not None
        await BuildEditView(build).send(interaction, ephemeral=True)
        return None


async def setup(bot: RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildEditCog(bot))
