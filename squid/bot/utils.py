"""Utility functions for the bot."""

import logging
import io
import inspect
from traceback import format_tb
from types import FrameType, TracebackType
from typing import TypedDict, Any
import mimetypes
import asyncio
import aiohttp

import discord
import bs4
from discord import Message, Webhook
from discord.abc import Messageable
from discord.ext.commands import Context, FlagConverter, NoPrivateMessage, MissingAnyRole, check, CheckFailure

from squid.bot import config
from squid.bot.config import OWNER_ID, PRINT_TRACEBACKS
from squid.db import DatabaseManager

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


logger = logging.getLogger(__name__)


def error_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_red, description=":x: " + description)


def warning_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=":warning: " + title, colour=discord_yellow, description=description)


def info_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx: Messageable | Webhook,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.delete_on_exit = delete_on_exit
        self.sent_message: Message

    async def __aenter__(self) -> Message:
        sent_message = await self.ctx.send(embed=info_embed(self.title, self.description))
        if sent_message is None:
            raise ValueError(
                "Failed to send message. (You are probably sending a message to a webhook, try looking into Webhook.send)"
            )

        self.sent_message = sent_message
        return sent_message

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool:
        # Handle exceptions
        if exc_type is not None:
            description = f"{str(exc_val)}"
            if PRINT_TRACEBACKS:
                description += f'\n\n```{"".join(format_tb(exc_tb))}```'
            await self.sent_message.edit(
                content=f"<@{OWNER_ID}>",
                embed=error_embed(f"An error has occurred: {exc_type.__name__}", description),
            )
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False


def check_is_owner_server(ctx: Context[Any]):
    """Check if the command is executed on the owner's server."""

    if not ctx.guild or not ctx.guild.id == config.OWNER_SERVER_ID:
        raise CheckFailure("This command can only be executed on certain servers.")
    return True


def check_is_staff():
    """Check if the user has a staff role, as defined in the server settings."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()

        server_id = ctx.guild.id
        staff_role_ids = await DatabaseManager().server_setting.get_single(server_id=server_id, setting="Staff")

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in staff_role_ids):  # type: ignore
            return True
        raise MissingAnyRole(list(staff_role_ids))

    return check(predicate)


async def is_staff(bot: discord.Client, server_id: int | None, user_id: int) -> bool:
    """Check if the user has a staff role, as defined in the server settings."""
    if server_id is None:
        return False  # TODO: global staff role

    staff_role_ids = await DatabaseManager().server_setting.get_single(server_id=server_id, setting="Staff")
    server = bot.get_guild(server_id)
    if server is None:
        return False
    member = server.get_member(user_id)
    if member is None:
        return False

    if set(staff_role_ids) & set(role.id for role in member.roles):
        return True
    return False


def check_is_trusted_or_staff():
    """Check if the user has a trusted or staff role, as defined in the server settings."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        db = DatabaseManager()
        server_id = ctx.guild.id
        staff_role_ids = await db.server_setting.get_single(server_id=server_id, setting="Staff")
        trusted_role_ids = await db.server_setting.get_single(server_id=server_id, setting="Trusted")
        allowed_role_ids = staff_role_ids + trusted_role_ids

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in allowed_role_ids):  # type: ignore
            return True
        raise MissingAnyRole(list(allowed_role_ids))

    return check(predicate)


class Preview(TypedDict):
    title: str | None
    description: str | None
    image: str | io.BytesIO | None
    site_name: str | None
    url: str | None


async def get_website_preview(url: str) -> Preview:
    """
    Fetches a webpage and tries to extract metadata in a manner similar to social platforms (e.g., Twitter or Discord previews).

    Returns:
        A dict with the following keys
        - title
        - description
        - image
        - site_name
        - url
    """
    preview: Preview = {
        "title": None,
        "description": None,
        "image": None,
        "site_name": None,
        "url": None,
    }

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": user_agent}) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                if not content_type:
                    content_type, _ = mimetypes.guess_type(url, strict=False)

                # If we can't find a content type, assume it's a webpage
                if not content_type:
                    logger.warning(f"Could not determine content type for URL '{url}'")
                    content_type = "text/html"

                # If it's a video, extract first frame
                if content_type.startswith("video/"):
                    preview["image"] = await extract_first_frame(url)
                    preview["url"] = url
                    return preview

                page_text = await response.text()
    except aiohttp.ClientError as e:
        logger.debug(f"Failed to retrieve URL '{url}': {e}")
        return preview

    soup = bs4.BeautifulSoup(page_text, "html.parser")

    def get_meta_content(property_name: str, attribute_type: str = "property") -> str | None:
        """Helper function to extract content from meta tags."""
        tag = soup.find("meta", attrs={attribute_type: property_name})

        assert not isinstance(tag, bs4.NavigableString), f"tag is a bs4.NavigableString: {tag}"
        if tag and tag.get("content"):
            content = tag["content"]
            assert isinstance(content, str), "tag['content'] is not a string"
            return content.strip()
        return None

    # Check Open Graph first (e.g. <meta property="og:title" content="..." />)
    preview["title"] = get_meta_content("og:title") or get_meta_content("twitter:title", "name")
    preview["description"] = get_meta_content("og:description") or get_meta_content("twitter:description", "name")
    preview["image"] = get_meta_content("og:image") or get_meta_content("twitter:image", "name")
    preview["site_name"] = get_meta_content("og:site_name")
    preview["url"] = get_meta_content("og:url")

    # Fallbacks if OG/Twitter meta not found:
    # title: <title> tag
    if not preview["title"]:
        if soup.title and soup.title.string:
            preview["title"] = soup.title.string.strip()
    # description: <meta name="description" content="..." />
    if not preview["description"]:
        preview["description"] = get_meta_content("description", "name")
    # site_name: domain from given url
    if not preview["site_name"]:
        preview["site_name"] = url.split("//")[-1].split("/")[0]
    # url: use the original
    if not preview["url"]:
        preview["url"] = url

    return preview


async def extract_first_frame(video_url: str) -> io.BytesIO:
    """
    Asynchronously extract the first frame from a remote video URL

    Args:
        video_url: the URL of the video to extract the frame from

    Returns:
        A BytesIO object containing the extracted frame

    Raises:
        RuntimeError: if the ffmpeg process fails
    """
    # fmt: off
    cmd = [
        "ffmpeg",
        "-i", video_url,
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1"
    ]
    # fmt: on

    # Run ffmpeg asynchronously
    # Note that you cannot use WindowsSelectorEventLoopPolicy with asyncio.run() on Windows
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg process failed. stderr: {err.decode('utf-8', errors='ignore')}")

    return io.BytesIO(out)


def fix_converter_annotations[_FlagConverter: type[FlagConverter]](cls: _FlagConverter) -> _FlagConverter:
    """
    Fixes discord.py being unable to evaluate annotations if `from __future__ import annotations` is used AND the `FlagConverter` is a nested class.

    This works because discord.py uses the globals() and locals() function to evaluate annotations at runtime.
    See https://discord.com/channels/336642139381301249/1328967235523317862 for more information about this.
    """
    previous_frame: FrameType = inspect.currentframe().f_back  # type: ignore
    previous_frame.f_globals[cls.__name__] = cls
    return cls


async def main():
    # image_url = "https://imgur.com/WwsKLH5"
    # video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    video_url2 = "https://files.catbox.moe/uadbru.mp4"
    preview = await get_website_preview(video_url2)
    print(preview)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())
