"""Handles the display of a build object."""

import asyncio
import logging
import mimetypes
from functools import partial
from typing import TYPE_CHECKING, Literal, cast, override

import discord
from discord.utils import escape_markdown

from squid.bot._types import GuildMessageable
from squid.bot.utils.components import (
    DISCORD_GREEN,
    DISCORD_RED,
    DISCORD_YELLOW,
    CardField,
    CardSection,
    StaticLayout,
    card_container,
    edit_layout,
    no_mentions,
    truncate_display_text,
)
from squid.bot.voting.build_session import BuildVoteSession
from squid.builds.domain import Build, Status
from squid.builds.domain.titles import format_build_display_title
from squid.core.concurrency import DISCORD_FANOUT_LIMIT, run_all

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

_SPONSOR_CREDIT_MAX_CHARACTERS = 255
_SPONSOR_WEBSITE_MAX_CHARACTERS = 512


class BuildHandler[BotT: "squid.bot.app.RedstoneSquid"]:
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

        match self.build.submission_status:
            case Status.PENDING:
                target = "Vote"
            case Status.DENIED:
                msg = "Denied submissions should not be posted."
                raise ValueError(msg)
            case Status.CONFIRMED:
                target = "Builds"
            case _:
                msg = "Invalid submission status"
                raise ValueError(msg)

        guild_channels = await self.bot.services.settings.get_many((guild.id for guild in self.bot.guilds), target)
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

        await BuildVoteSession.ensure_submission(self.bot, build, await self.get_channels_to_post_to())

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
        assert self.build.id is not None, "Persisted display messages require a build ID"
        messages = await self.bot.services.messages.list_for_build(self.build.id, self.bot.user.id)
        maybe_messages = await run_all(
            [
                partial(self.bot.get_or_fetch_message, row.channel_id, row.id)
                for row in messages
                if row.channel_id is not None
            ],
            limit=DISCORD_FANOUT_LIMIT,
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
            layout_task = tg.create_task(self.render_layout())

        messages = await msg_task
        layout = await layout_task

        async def _update_single_message(message: discord.Message):
            await edit_layout(message, layout, allowed_mentions=no_mentions())
            await self.bot.services.messages.update_edited_time(message.id)

        await run_all(
            [partial(_update_single_message, message) for message in messages],
            limit=DISCORD_FANOUT_LIMIT,
        )

    async def render_layout(self) -> StaticLayout:
        """Render a standalone Components V2 layout for the build."""
        return StaticLayout(await self.render_container())

    async def render_container(self) -> discord.ui.Container[discord.ui.LayoutView]:
        """Render the build card for composition into a larger V2 layout."""
        build = self.build
        current_java_version = await self.bot.services.versions.newest("Java")
        metadata = self.get_metadata_fields()
        performance_names = {
            "Dimensions",
            "Volume",
            "Opening Time",
            "Closing Time",
            "Visible Opening Time",
            "Visible Closing Time",
        }
        resource_names = {
            "Server",
            "Coordinates",
            "Command",
            "World Download",
            "Schematic",
            "Videos",
            "Sponsor Website",
        }
        credit_names = {"Creators", "Date Of Completion", "Sponsoring Server"}
        review_names = {"⚠ Possible duplicate"}

        def section(title: str, names: set[str]) -> CardSection:
            return CardSection(
                title,
                tuple(CardField(name, escape_markdown(value)) for name, value in metadata.items() if name in names),
            )

        status_colours: dict[Status | None, int] = {
            Status.PENDING: DISCORD_YELLOW,
            Status.CONFIRMED: DISCORD_GREEN,
            Status.DENIED: DISCORD_RED,
        }
        footer = f"Submission ID: {build.id}"
        if build.edited_time is not None:
            footer += f" • Updated <t:{int(build.edited_time.timestamp())}:R>"
        container = card_container(
            format_build_display_title(build, markdown=True, current_version=current_java_version),
            await self.get_description(),
            accent_colour=status_colours.get(build.submission_status, DISCORD_GREEN),
            sections=(
                section("Review warnings", review_names),
                section("Size & performance", performance_names),
                section("Compatibility", {"Versions"}),
                section("Credits", credit_names),
                section("Resources", resource_names),
            ),
            footer=footer,
            media=await self._get_media_urls(),
        )
        if build.original_link is not None:
            container.add_item(
                discord.ui.ActionRow(
                    discord.ui.Button(label="Original submission", url=build.original_link),
                )
            )
        return container

    async def _get_media_urls(self) -> list[str]:
        media: list[str] = []
        for url in self.build.image_urls:
            mimetype, _ = mimetypes.guess_type(url)
            if mimetype is not None and mimetype.startswith("image"):
                media.append(url)
                continue
            try:
                preview = await self.bot.media_previews.get(url)
            except Exception:
                logger.warning("Could not resolve a build media preview", exc_info=True)
                continue
            image = preview["image"]
            if isinstance(image, str):
                media.append(image)

        if not media:
            for url in self.build.video_urls:
                try:
                    preview = await self.bot.media_previews.get(url)
                except Exception:
                    logger.warning("Could not resolve a build video preview", exc_info=True)
                    continue
                image = preview["image"]
                if isinstance(image, str):
                    media.append(image)
                    break

        # Appended last so a generated render only becomes media[0] — the card's thumbnail —
        # when the build has no real screenshot to show instead.
        media.extend(self.build.render_urls)

        return media[:10]

    async def get_description(self) -> str | None:  # type: ignore
        """Generates a description for the build, which includes component restrictions, version compatibility, and other information."""
        build = self.build
        desc = []

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

        if sponsor := build.sponsor:
            credit = sponsor.display_name or sponsor.address or "Public Paper server"
            fields["Sponsoring Server"] = truncate_display_text(credit, _SPONSOR_CREDIT_MAX_CHARACTERS)
            if sponsor.website_url is not None:
                fields["Sponsor Website"] = truncate_display_text(
                    sponsor.website_url,
                    _SPONSOR_WEBSITE_MAX_CHARACTERS,
                )

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
        if build.schematic_urls:
            fields["Schematic"] = ", ".join(build.schematic_urls)
        if build.video_urls:
            fields["Videos"] = ", ".join(build.video_urls)

        if duplicates := build.extra_info.get("schematic_duplicates"):
            labels = {
                "identical": "byte-identical file",
                "structural-match": "same structure, moved or rotated",
                "near": "near structural match",
            }
            fields["⚠ Possible duplicate"] = "\n".join(
                f"Build #{candidate['build_id']} ({labels[candidate['tier']]})" for candidate in duplicates
            )

        return fields
