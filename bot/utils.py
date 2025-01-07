"""Utility functions for the bot."""

from __future__ import annotations

import re
from traceback import format_tb
from types import TracebackType
from typing import overload, Literal, TYPE_CHECKING, Any, Mapping, cast

import discord
from discord import Message, Webhook
from discord.abc import Messageable
from discord.ext.commands import Context, CommandError, NoPrivateMessage, MissingAnyRole, check
from pydantic import TypeAdapter, ValidationError

from bot import config
from bot._types import GuildMessageable
from bot.config import OWNER_ID, PRINT_TRACEBACKS
from database.message import untrack_message
from database.schema import (
    MessageRecord,
    DeleteLogVoteSessionRecord,
)
from database.server_settings import get_server_setting

if TYPE_CHECKING:
    pass

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


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


def is_owner_server(ctx: Context[Any]):
    """Check if the command is executed on the owner's server."""

    if not ctx.guild or not ctx.guild.id == config.OWNER_SERVER_ID:
        # TODO: Make a custom error for this.
        # https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=is_owner#discord.discord.ext.commands.on_command_error
        raise CommandError("This command can only be executed on certain servers.")
    return True


def check_is_staff():
    """Check if the user has a staff role, as defined in the server settings."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()

        server_id = ctx.guild.id
        staff_role_ids = await get_server_setting(server_id=server_id, setting="Staff")

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in staff_role_ids):  # type: ignore
            return True
        raise MissingAnyRole(list(staff_role_ids))

    return check(predicate)


async def is_staff(bot: discord.Client, server_id: int | None, user_id: int) -> bool:
    """Check if the user has a staff role, as defined in the server settings."""
    if server_id is None:
        return False  # TODO: global staff role

    staff_role_ids = await get_server_setting(server_id=server_id, setting="Staff")
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

        server_id = ctx.guild.id
        staff_role_ids = await get_server_setting(server_id=server_id, setting="Staff")
        trusted_role_ids = await get_server_setting(server_id=server_id, setting="Trusted")
        allowed_role_ids = staff_role_ids + trusted_role_ids

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in allowed_role_ids):  # type: ignore
            return True
        raise MissingAnyRole(list(allowed_role_ids))

    return check(predicate)


@overload
async def getch(bot: discord.Client, record: MessageRecord | DeleteLogVoteSessionRecord) -> Message | None: ...


async def getch(bot: discord.Client, record: Mapping[str, Any]) -> Any:
    """Fetch discord objects from database records."""

    try:
        message_adapter = TypeAdapter(MessageRecord)
        message_adapter.validate_python(record)
        return await getch_message(bot, record["channel_id"], record["message_id"])
    except ValidationError:
        pass

    try:
        message_adapter = TypeAdapter(DeleteLogVoteSessionRecord)
        message_adapter.validate_python(record)
        return await getch_message(bot, record["target_channel_id"], record["target_message_id"])
    except ValidationError:
        pass

    raise ValueError("Invalid object to fetch.")


async def getch_message(bot: discord.Client, channel_id: int, message_id: int) -> Message | None:
    """Fetch a message from a channel."""

    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await bot.fetch_channel(channel_id)
    channel = cast(GuildMessageable, channel)
    assert isinstance(channel, GuildMessageable), f"{type(channel)=}"
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        await untrack_message(message_id)
    except discord.Forbidden:
        pass
    return None


async def main():
    pass


if __name__ == "__main__":
    from dotenv import load_dotenv
    import asyncio

    load_dotenv()
    asyncio.run(main())
