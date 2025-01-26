"""This module contains a mixin for the Build class that adds Discord-related functionality."""
from __future__ import annotations

import io
import re
import asyncio
from dataclasses import dataclass, field
import mimetypes
from typing import TYPE_CHECKING, Any, Literal, cast

from discord.utils import escape_markdown

from squid.bot._types import GuildMessageable
import squid.bot.utils as bot_utils
from squid.db import DatabaseManager
from squid.db.schema import Status
from squid.db.utils import upload_to_catbox, utcnow

if TYPE_CHECKING:
    import discord
    from squid.db.builds import Build
    from squid.bot.main import RedstoneSquid


background_tasks: set[asyncio.Task[Any]] = set()


@dataclass
class DiscordBuildMixin:
    """A mixin for the Build class that adds Discord-related functionality."""

    _original_message_obj: discord.Message | None = field(default=None, init=False, repr=False)
    """Cache for the original message of the build."""

    async def get_channels_to_post_to(self: Build, bot: RedstoneSquid) -> list[GuildMessageable]:  # type: ignore
        """
        Gets the channels in which this build should be posted to.

        Args:
            bot: A bot instance to get the channels from.
        """

        target: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]

        match (self.submission_status, self.record_category):
            case (Status.PENDING, _):
                target = "Vote"
            case (Status.DENIED, _):
                raise ValueError("Denied submissions should not be posted.")
            case (Status.CONFIRMED, None):
                target = "Builds"
            case (Status.CONFIRMED, "Smallest"):
                target = "Smallest"
            case (Status.CONFIRMED, "Fastest"):
                target = "Fastest"
            case (Status.CONFIRMED, "First"):
                target = "First"
            case _:
                raise ValueError("Invalid status or record category")

        guild_channels = await bot.db.server_setting.get((guild.id for guild in bot.guilds), target)
        maybe_channels = [bot.get_channel(channel_id) for channel_id in guild_channels.values() if channel_id is not None]
        channels = [channel for channel in maybe_channels if channel is not None]
        return cast(list[GuildMessageable], channels)

    async def get_original_message(self: Build, bot: RedstoneSquid) -> discord.Message | None:  # type: ignore
        """Gets the original message of the build."""
        if self._original_message_obj:
            return self._original_message_obj

        if self.original_channel_id:
            assert self.original_message_id is not None
            return await bot.get_or_fetch_message(self.original_channel_id, self.original_message_id)
        return None

    async def update_messages(self: Build, bot: RedstoneSquid) -> None:  # type: ignore
        """Updates all messages which for this build."""
        if self.id is None:
            raise ValueError("Build id is None.")

        # Get all messages for a build
        async with asyncio.TaskGroup() as tg:
            msg_task = tg.create_task(self.get_display_messages())
            em_task = tg.create_task(self.generate_embed())

        message_records = await msg_task
        em = await em_task

        for record in message_records:
            message = await bot.get_or_fetch_message(record["channel_id"], record["message_id"])
            if message is None:
                continue
            await message.edit(content=self.original_link, embed=em)
            await bot.db.message.update_message_edited_time(message)

    async def generate_embed(self: Build) -> discord.Embed:  # type: ignore
        """Generates an embed for the build."""
        em = bot_utils.info_embed(title=self.get_title(), description=await self.get_description())

        fields = self.get_metadata_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=escape_markdown(val), inline=True)

        if self.image_urls:
            for url in self.image_urls:
                mimetype, _ = mimetypes.guess_type(url)
                if mimetype is not None and mimetype.startswith("image"):
                    em.set_image(url=url)
                    break
                else:
                    preview = await bot_utils.get_website_preview(url)
                    if isinstance(preview["image"], io.BytesIO):
                        raise RuntimeError("Got a BytesIO object instead of a URL.")
                    em.set_image(url=preview["image"])
        elif self.video_urls:
            for url in self.video_urls:
                preview = await bot_utils.get_website_preview(url)
                if image := preview["image"]:
                    if isinstance(image, str):
                        em.set_image(url=image)
                    else:  # isinstance(image, io.BytesIO)
                        preview_url = await upload_to_catbox(
                            filename="video_preview.png", file=image, mimetype="image/png"
                        )
                        self.image_urls.append(preview_url)
                        if self.id is not None:
                            background_tasks.add(
                                asyncio.create_task(
                                    DatabaseManager()
                                    .table("build_links")
                                    .insert({"build_id": self.id, "url": preview_url, "media_type": "image"})
                                    .execute()
                                )
                            )
                        em.set_image(url=preview_url)
                    break

        em.set_footer(text=f"Submission ID: {self.id} • Last Update {utcnow()}")
        return em

    def get_title(self: Build) -> str:  # type: ignore
        """Generates the official Redstone Squid defined title for the build."""
        title = ""

        if self.category != "Door":
            raise NotImplementedError("Only doors are supported for now.")

        if self.submission_status == Status.PENDING:
            title += "Pending: "
        elif self.submission_status == Status.DENIED:
            title += "Denied: "
        if self.ai_generated:
            title += "\N{ROBOT FACE}"
        if self.record_category:
            title += f"{self.record_category} "

        # Special casing misc restrictions shaped like "0.3s" and "524 Blocks"
        for restriction in self.information.get("unknown_restrictions", {}).get("miscellaneous_restrictions", []):
            if re.match(r"\d+\.\d+\s*s", restriction):
                title += f"{restriction} "
            elif re.match(r"\d+\s*[Bb]locks", restriction):
                title += f"{restriction} "

        # FIXME: This is included in the title for now to match people's expectations
        for restriction in self.component_restrictions:
            title += f"{restriction} "
        for restriction in self.information.get("unknown_restrictions", {}).get("component_restrictions", []):
            title += f"*{restriction}* "

        # Door dimensions
        if self.door_width and self.door_height and self.door_depth and self.door_depth > 1:
            title += f"{self.door_width}x{self.door_height}x{self.door_depth} "
        elif self.door_width and self.door_height:
            title += f"{self.door_width}x{self.door_height} "
        elif self.door_width:
            title += f"{self.door_width} Wide "
        elif self.door_height:
            title += f"{self.door_height} High "

        # Wiring Placement Restrictions
        for restriction in self.wiring_placement_restrictions:
            title += f"{restriction} "

        for restriction in self.information.get("unknown_restrictions", {}).get("wiring_placement_restrictions", []):
            title += f"*{restriction}* "

        # Pattern
        for pattern in self.door_type:
            if pattern != "Regular":
                title += f"{pattern} "

        for pattern in self.information.get("unknown_patterns", []):
            title += f"*{pattern}* "

        # Door type
        if self.door_orientation_type is None:
            raise ValueError("Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing.")
        title += self.door_orientation_type

        return title

    async def get_description(self: Build) -> str | None:  # type: ignore
        """Generates a description for the build, which includes component restrictions, version compatibility, and other information."""
        desc = []

        if self.component_restrictions and self.component_restrictions[0] != "None":
            desc.append(", ".join(self.component_restrictions))

        if await DatabaseManager().get_or_fetch_newest_version(edition="Java") not in self.versions:
            desc.append("**Broken** in current (Java) version.")

        if "Locational" in self.miscellaneous_restrictions:
            desc.append("**Locational**.")
        elif "Locational with fixes" in self.miscellaneous_restrictions:
            desc.append("**Locational** with known fixes for each location.")

        if "Directional" in self.miscellaneous_restrictions:
            desc.append("**Directional**.")
        elif "Directional with fixes" in self.miscellaneous_restrictions:
            desc.append("**Directional** with known fixes for each direction.")

        if self.information and (user_message := self.information.get("user")):
            desc.append("\n" + escape_markdown(user_message))

        return "\n".join(desc) if desc else None

    def get_metadata_fields(self: Build) -> dict[str, str]:  # type: ignore
        """Returns a dictionary of metadata fields for the build.

        The fields are formatted as key-value pairs, where the key is the field name and the value is the field value. The values are not escaped."""
        fields = {"Dimensions": f"{self.width or '?'} x {self.height or '?'} x {self.depth or '?'}"}

        if self.width and self.height and self.depth:
            fields["Volume"] = str(self.width * self.height * self.depth)

        # The times are stored as game ticks, so they need to be divided by 20 to get seconds
        if self.normal_opening_time:
            fields["Opening Time"] = f"{self.normal_opening_time / 20}s"
        if self.normal_closing_time:
            fields["Closing Time"] = f"{self.normal_closing_time / 20}s"
        if self.visible_opening_time:
            fields["Visible Opening Time"] = f"{self.visible_opening_time / 20}s"
        if self.visible_closing_time:
            fields["Visible Closing Time"] = f"{self.visible_closing_time / 20}s"

        if self.creators_ign:
            fields["Creators"] = ", ".join(sorted(self.creators_ign))

        if self.completion_time:
            fields["Date Of Completion"] = str(self.completion_time)

        fields["Versions"] = self.version_spec or "Unknown"

        if ip := self.server_info.get("server_ip"):
            fields["Server"] = ip
            if coordinates := self.server_info.get("coordinates"):
                fields["Coordinates"] = coordinates
            if command := self.server_info.get("command_to_build"):
                fields["Command"] = command

        if self.world_download_urls:
            fields["World Download"] = ", ".join(self.world_download_urls)
        if self.video_urls:
            fields["Videos"] = ", ".join(self.video_urls)

        return fields
