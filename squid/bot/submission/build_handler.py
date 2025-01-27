"""Handles the display of a build object."""

from __future__ import annotations

import asyncio
import io
import mimetypes
from typing import TYPE_CHECKING, Any, Literal, cast, override

import discord
from discord.utils import escape_markdown

import squid.bot.utils as bot_utils
from squid.bot._types import GuildMessageable
from squid.bot.voting.vote_session import BuildVoteSession
from squid.db import DatabaseManager
from squid.db.builds import Build
from squid.db.schema import Status
from squid.db.utils import upload_to_catbox, utcnow

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


background_tasks: set[asyncio.Task[Any]] = set()


class BuildHandler[BotT: RedstoneSquid]:
    """A class to handle the display of a build object."""

    def __init__(self, bot: BotT, build: Build):
        self.bot = bot
        self.build = build
        self._build_original_message_obj: discord.Message | None = None
        """Cache for the original message of the build."""

    @override
    def __repr__(self):
        return f"<BuildHandler(bot={self.bot}, build={self.build})>"

    async def get_channels_to_post_to(self) -> list[GuildMessageable]:  # type: ignore
        """Gets the channels in which this build should be posted to."""

        target: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]

        match (self.build.submission_status, self.build.record_category):
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

        guild_channels = await self.bot.db.server_setting.get((guild.id for guild in self.bot.guilds), target)
        maybe_channels = [
            self.bot.get_channel(channel_id) for channel_id in guild_channels.values() if channel_id is not None
        ]
        channels = [channel for channel in maybe_channels if channel is not None]
        return cast(list[GuildMessageable], channels)

    async def post_for_voting(self, type: Literal["add", "update"] = "add") -> None:
        """
        Post a build for voting.

        Args:
            type (Literal["add", "update"]): Whether to add or update the build.
        """
        build = self.build
        if type == "update":
            raise NotImplementedError("Updating builds is not yet implemented.")

        if build.submission_status != Status.PENDING:
            raise ValueError("The build must be pending to post it.")

        em = await self.generate_embed()
        messages = await asyncio.gather(
            *(
                vote_channel.send(content=build.original_link, embed=em)
                for vote_channel in await self.get_channels_to_post_to()
            )
        )

        assert build.submitter_id is not None
        await BuildVoteSession.create(self.bot, messages, build.submitter_id, build, type)

    async def get_original_message(self) -> discord.Message | None:  # type: ignore
        """Gets the original message of the build."""
        if self._build_original_message_obj:
            return self._build_original_message_obj

        if self.build.original_channel_id:
            assert self.build.original_message_id is not None
            return await self.bot.get_or_fetch_message(self.build.original_channel_id, self.build.original_message_id)
        return None

    async def update_messages(self) -> None:  # type: ignore
        """Updates all messages which for this build."""
        if self.build.id is None:
            raise ValueError("Build id is None.")

        # Get all messages for a build
        async with asyncio.TaskGroup() as tg:
            msg_task = tg.create_task(self.build.get_display_messages())
            em_task = tg.create_task(self.generate_embed())

        message_records = await msg_task
        em = await em_task

        for record in message_records:
            message = await self.bot.get_or_fetch_message(record["channel_id"], record["message_id"])
            if message is None:
                continue
            await message.edit(content=self.build.original_link, embed=em)
            await self.bot.db.message.update_message_edited_time(message)

    async def generate_embed(self) -> discord.Embed:  # type: ignore
        """Generates an embed for the build."""
        build = self.build
        em = bot_utils.info_embed(title=self.build.get_title(), description=await self.get_description())

        fields = self.get_metadata_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=escape_markdown(val), inline=True)

        if build.image_urls:
            for url in build.image_urls:
                mimetype, _ = mimetypes.guess_type(url)
                if mimetype is not None and mimetype.startswith("image"):
                    em.set_image(url=url)
                    break
                else:
                    preview = await bot_utils.get_website_preview(url)
                    if isinstance(preview["image"], io.BytesIO):
                        raise RuntimeError("Got a BytesIO object instead of a URL.")
                    em.set_image(url=preview["image"])
        elif build.video_urls:
            for url in build.video_urls:
                preview = await bot_utils.get_website_preview(url)
                if image := preview["image"]:
                    if isinstance(image, str):
                        em.set_image(url=image)
                    else:  # isinstance(image, io.BytesIO)
                        preview_url = await upload_to_catbox(
                            filename="video_preview.png", file=image, mimetype="image/png"
                        )
                        build.image_urls.append(preview_url)
                        if build.id is not None:
                            background_tasks.add(
                                asyncio.create_task(
                                    DatabaseManager()
                                    .table("build_links")
                                    .insert({"build_id": build.id, "url": preview_url, "media_type": "image"})
                                    .execute()
                                )
                            )
                        em.set_image(url=preview_url)
                    break

        em.set_footer(text=f"Submission ID: {build.id} • Last Update {utcnow()}")
        return em

    async def get_description(self) -> str | None:  # type: ignore
        """Generates a description for the build, which includes component restrictions, version compatibility, and other information."""
        build = self.build
        desc = []

        if build.component_restrictions and build.component_restrictions[0] != "None":
            desc.append(", ".join(build.component_restrictions))

        if await DatabaseManager().get_or_fetch_newest_version(edition="Java") not in build.versions:
            desc.append("**Broken** in current (Java) version.")

        if "Locational" in build.miscellaneous_restrictions:
            desc.append("**Locational**.")
        elif "Locational with fixes" in build.miscellaneous_restrictions:
            desc.append("**Locational** with known fixes for each location.")

        if "Directional" in build.miscellaneous_restrictions:
            desc.append("**Directional**.")
        elif "Directional with fixes" in build.miscellaneous_restrictions:
            desc.append("**Directional** with known fixes for each direction.")

        if build.information and (user_message := build.information.get("user")):
            desc.append("\n" + escape_markdown(user_message))

        return "\n".join(desc) if desc else None

    def get_metadata_fields(self) -> dict[str, str]:  # type: ignore
        """Returns a dictionary of metadata fields for the build.

        The fields are formatted as key-value pairs, where the key is the field name and the value is the field value. The values are not escaped."""
        build = self.build
        fields = {"Dimensions": f"{build.width or '?'} x {build.height or '?'} x {build.depth or '?'}"}

        if build.width and build.height and build.depth:
            fields["Volume"] = str(build.width * build.height * build.depth)

        # The times are stored as game ticks, so they need to be divided by 20 to get seconds
        if build.normal_opening_time:
            fields["Opening Time"] = f"{build.normal_opening_time / 20}s"
        if build.normal_closing_time:
            fields["Closing Time"] = f"{build.normal_closing_time / 20}s"
        if build.visible_opening_time:
            fields["Visible Opening Time"] = f"{build.visible_opening_time / 20}s"
        if build.visible_closing_time:
            fields["Visible Closing Time"] = f"{build.visible_closing_time / 20}s"

        if build.creators_ign:
            fields["Creators"] = ", ".join(sorted(build.creators_ign))

        if build.completion_time:
            fields["Date Of Completion"] = str(build.completion_time)

        fields["Versions"] = build.version_spec or "Unknown"

        if ip := build.server_info.get("server_ip"):
            fields["Server"] = ip
            if coordinates := build.server_info.get("coordinates"):
                fields["Coordinates"] = coordinates
            if command := build.server_info.get("command_to_build"):
                fields["Command"] = command

        if build.world_download_urls:
            fields["World Download"] = ", ".join(build.world_download_urls)
        if build.video_urls:
            fields["Videos"] = ", ".join(build.video_urls)

        return fields


async def main():
    from squid.db.builds import Build

    build = await Build.from_id(1)
    assert build is not None


if __name__ == "__main__":
    asyncio.run(main())
