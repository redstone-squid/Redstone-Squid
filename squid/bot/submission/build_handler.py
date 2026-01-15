"""Handles the display of a build object."""

import asyncio
import io
import mimetypes
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, cast, override

import discord
from discord.utils import escape_markdown
from sqlalchemy import insert, select

import squid.bot.utils as bot_utils
from squid.bot._types import GuildMessageable
from squid.bot.voting.vote_session import BuildVoteSession
from squid.db import DatabaseManager
from squid.db.builds import Build
from squid.db.schema import BuildLink, Message, Status
from squid.utils import upload_to_catbox, utcnow

if TYPE_CHECKING:
    import squid.bot


background_tasks: set[asyncio.Task[Any]] = set()


class BuildHandler[BotT: "squid.bot.RedstoneSquid"]:
    """A class to handle the display of a build object."""

    def __init__(self, bot: BotT, build: Build):
        self.bot = bot
        self.build = build
        self._build_original_message_obj: discord.Message | None = None
        """Cache for the original message of the build."""

    @override
    def __repr__(self):
        return f"<BuildHandler(bot={self.bot}, build={self.build})>"

    async def get_channels_to_post_to(self) -> list[GuildMessageable]:
        """Gets the channels in which this build should be posted to."""

        target: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]

        match (self.build.submission_status, self.build.record_category):
            case (Status.PENDING, _):
                target = "Vote"
            case (Status.DENIED, _):
                msg = "Denied submissions should not be posted."
                raise ValueError(msg)
            case (Status.CONFIRMED, None):
                target = "Builds"
            case (Status.CONFIRMED, "Smallest"):
                target = "Smallest"
            case (Status.CONFIRMED, "Fastest"):
                target = "Fastest"
            case (Status.CONFIRMED, "First"):
                target = "First"
            case _:
                msg = "Invalid status or record category"
                raise ValueError(msg)

        guild_channels = await self.bot.db.server_setting.get((guild.id for guild in self.bot.guilds), target)
        maybe_channels = [
            self.bot.get_channel(channel_id) for channel_id in guild_channels.values() if channel_id is not None
        ]
        channels = [channel for channel in maybe_channels if channel is not None]
        # noinspection PyTypeHints: PyCharm thinks this cast is invalid
        return cast(list[GuildMessageable], channels)

    async def post_for_voting(self, type: Literal["add", "update"] = "add") -> None:
        """
        Post a build for voting.

        Args:
            type (Literal["add", "update"]): Whether to add or update the build.
        """
        build = self.build
        if type == "update":
            msg = "Updating builds is not yet implemented."
            raise NotImplementedError(msg)

        if build.submission_status != Status.PENDING:
            msg = "The build must be pending to post it."
            raise ValueError(msg)

        em = await self.generate_embed()
        messages = await asyncio.gather(
            *(
                vote_channel.send(content=build.original_link, embed=em)
                for vote_channel in await self.get_channels_to_post_to()
            )
        )

        assert build.submitter_id is not None
        await BuildVoteSession.create(self.bot, messages, build.submitter_id, build, type)

    async def get_original_message(self) -> discord.Message | None:
        """Gets the original message of the build."""
        if self._build_original_message_obj:
            return self._build_original_message_obj

        if self.build.original_channel_id:
            assert self.build.original_message_id is not None
            return await self.bot.get_or_fetch_message(self.build.original_channel_id, self.build.original_message_id)
        return None

    async def get_display_messages(self) -> list[discord.Message]:
        """Get all messages from the bot that are related to this build.

        This does not include messages from other users, only the bot's messages.
        """
        assert self.bot.user is not None, "Bot should be logged in"
        stmt = select(Message).where(Message.build_id == self.build.id, Message.author_id == self.bot.user.id)
        async with self.bot.db.async_session() as session:
            result = await session.execute(stmt)
            messages: Sequence[Message] = result.scalars().all()
        maybe_messages = await asyncio.gather(
            *(self.bot.get_or_fetch_message(row.channel_id, row.id) for row in messages if row.channel_id is not None)
        )
        return [msg for msg in maybe_messages if msg is not None]

    async def update_messages(self) -> None:
        """Updates all messages which for this build."""
        if self.build.id is None:
            msg = "Build id is None."
            raise ValueError(msg)

        # Get all messages for a build
        async with asyncio.TaskGroup() as tg:
            msg_task = tg.create_task(self.get_display_messages())
            em_task = tg.create_task(self.generate_embed())

        messages = await msg_task
        em = await em_task

        async def _update_single_message(message: discord.Message):
            await message.edit(content=self.build.original_link, embed=em)
            await self.bot.db.message.update_message_edited_time(message)

        await asyncio.gather(*(_update_single_message(message) for message in messages))

    async def _insert_video_preview(self, preview_url: str) -> None:
        """Insert a video preview into the database."""
        async with self.bot.db.async_session() as session:
            stmt = insert(BuildLink).values(build_id=self.build.id, url=preview_url, media_type="image")
            await session.execute(stmt)
            await session.commit()

    async def generate_embed(self) -> discord.Embed:
        """Generates an embed for the build."""
        build = self.build
        em = bot_utils.info_embed(title=self.build.title, description=await self.get_description())

        fields = self.get_metadata_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=escape_markdown(val), inline=True)

        if build.image_urls:
            for url in build.image_urls:
                mimetype, _ = mimetypes.guess_type(url)
                if mimetype is not None and mimetype.startswith("image"):
                    em.set_image(url=url)
                    break
                preview = await bot_utils.get_website_preview(url)
                if isinstance(preview["image"], io.BytesIO):
                    msg = "Got a BytesIO object instead of a URL."
                    raise RuntimeError(msg)
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
                            task = asyncio.create_task(self._insert_video_preview(preview_url))
                            background_tasks.add(task)
                            task.add_done_callback(background_tasks.discard)
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

        if build.extra_info and (user_message := build.extra_info.get("user")):
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

        server_info = build.extra_info.get("server_info", {})
        if ip := server_info.get("server_ip"):
            fields["Server"] = ip
            if coordinates := server_info.get("coordinates"):
                fields["Coordinates"] = coordinates
            if command := server_info.get("command_to_build"):
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
