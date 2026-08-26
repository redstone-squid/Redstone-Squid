"""Handles the display of a build object."""

import logging
import mimetypes
from typing import TYPE_CHECKING, Literal, cast, override

import discord
from discord.utils import escape_markdown

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.ui import (
    DISCORD_GREEN,
    DISCORD_RED,
    DISCORD_YELLOW,
    render_item,
    render_presentation,
    render_static,
    truncate_display_text,
)
from squid.bot.voting.sessions import configured_vote_channels, ensure_build_review
from squid.builds.domain import Build, DoorBuild, Status
from squid.builds.domain.titles import format_build_display_title

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

        target: Literal["Smallest", "Fastest", "First", "Builds"]

        match self.build.submission_status:
            case Status.PENDING:
                return await configured_vote_channels(self.bot)
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

        await ensure_build_review(self.bot, build, await self.get_channels_to_post_to())

    async def get_original_message(self) -> discord.Message | None:
        """Gets the message this build was submitted from, if it is still reachable."""
        if self._build_original_message_obj:
            return self._build_original_message_obj

        for source in self.build.source_messages:
            if source.channel_id is not None:
                return await self.bot.get_or_fetch_message(source.channel_id, source.message_id)
        return None

    async def render_layout(self) -> sd.message_payload.MessagePayload:
        """Render a standalone Components V2 presentation for the build."""
        return render_static([await self.render_node()])

    async def render_presentation(self) -> sd.message_payload.MessagePayload:
        """Render the complete presentation used by post delivery."""
        return render_presentation([await self.render_node()])

    async def render_container(
        self, *, reservation: sd.ResourceCost = sd.EMPTY_RESERVATION
    ) -> discord.ui.Container[discord.ui.LayoutView]:
        """Render the build card as a detached item, for composition into a larger V2 layout.

        ``reservation`` withholds whatever the caller spends on the rest of the message, so
        the card shrinks to leave room for content the solver cannot see.
        """
        container = render_item(await self.render_node(), reservation=reservation)
        assert isinstance(container, discord.ui.Container)
        return container

    async def render_node(self) -> sl.LayoutNode:
        """The build card as layout IR, for callers composing a whole message at once."""
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

        ladders = self._field_ladders()

        # A nested section per group: each field steps its own Condense ladder independently
        # rather than a whole group stepping in lockstep — finer granularity, not a
        # regression. Groups with no matching fields render as nothing.
        def group(title: str, names: set[str]) -> sl.LayoutNode | None:
            entries = tuple(
                sl.field(
                    name,
                    escape_markdown(value),
                    fallbacks=tuple(escape_markdown(alt) for alt in ladders.get(name, ())),
                )
                for name, value in metadata.items()
                if name in names
            )
            return sl.section(sl.heading(title), sl.fields(*entries)) if entries else None

        status_colours: dict[Status | None, int] = {
            Status.PENDING: DISCORD_YELLOW,
            Status.CONFIRMED: DISCORD_GREEN,
            Status.DENIED: DISCORD_RED,
        }
        footer = f"Submission ID: {build.id}"
        if build.edited_time is not None:
            footer += f" • Updated <t:{build.edited_time.timestamp()}:R>"
        rows = ()
        if build.original_link is not None:
            rows = (
                sl.action_controls(
                    sl.link("Original submission", build.original_link, key="original-submission"),
                    key="submission-links",
                ),
            )
        description = await self.get_description()
        media = await self._get_media_urls()
        extra_media = media[1:]
        return sl.section(
            sl.heading(format_build_display_title(build, markdown=True, current_version=current_java_version)),
            # The body is the card's shock absorber: truncate lets it give up characters
            # under pressure before a field group, media, or the footer loses any.
            description and sl.truncate(sl.paragraph(description)),
            group("Review warnings", review_names),
            group("Size & performance", performance_names),
            group("Compatibility", {"Versions"}),
            group("Credits", credit_names),
            group("Resources", resource_names),
            bool(extra_media) and sl.media(*extra_media, key="media"),
            sl.note(footer),
            *rows,
            accent=status_colours.get(build.submission_status, DISCORD_GREEN),
            thumbnail=media[0] if media else None,
        )

    def _field_ladders(self) -> dict[str, tuple[str, ...]]:
        """Degradation ladders for the fields whose values are unbounded user data.

        A build can carry a hundred URLs per list; showing them all is the preferred form,
        but under budget pressure a count beats a mid-URL ellipsis.
        """
        build = self.build
        ladders: dict[str, tuple[str, ...]] = {}
        for name, urls in (
            ("World Download", build.world_download_urls),
            ("Schematic", build.schematic_urls),
            ("Videos", build.video_urls),
        ):
            if len(urls) > 1:
                ladders[name] = (f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links")
        creators = sorted(build.creators_ign)
        if len(creators) > 3:
            ladders["Creators"] = (f"{creators[0]} and {len(creators) - 1} others",)
        return ladders

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
        if isinstance(build, DoorBuild):
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
            fields["Date Of Completion"] = build.completion_time

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
