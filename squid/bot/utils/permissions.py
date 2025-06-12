"""Permission checking utilities for the bot."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from discord.ext.commands import CheckFailure, Context, MissingAnyRole, NoPrivateMessage, check

from squid.db import DatabaseManager

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


def check_is_owner_server():
    """Check if the command is executed on the owner's server."""

    async def predicate(ctx: Context[RedstoneSquid]) -> bool:
        if ctx.bot.owner_server_id is None:
            return True  # No owner server set, so we allow the command to run anywhere

        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.guild.id == ctx.bot.owner_server_id:
            return True
        raise CheckFailure("This command can only be executed on certain servers.")

    return check(predicate)


def is_owner_server(bot: RedstoneSquid, server_id: int) -> bool:
    """Check if the server is the owner's server."""
    return server_id == bot.owner_server_id


@cache
def check_is_staff():
    """Check if the user has a staff role, as defined in the server settings."""

    async def predicate(ctx: Context[RedstoneSquid]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()

        server_id = ctx.guild.id
        staff_role_ids = await ctx.bot.db.server_setting.get_single(server_id=server_id, setting="Staff")

        # ctx.guild is None doesn't narrow ctx.author to Member
        if any(ctx.author.get_role(item) is not None for item in staff_role_ids):  # type: ignore
            return True
        raise MissingAnyRole(list(staff_role_ids))

    return check(predicate)


async def is_staff(bot: RedstoneSquid, server_id: int | None, user_id: int) -> bool:
    """Check if the user has a staff role, as defined in the server settings."""
    if server_id is None:
        return False  # TODO: global staff role

    staff_role_ids = await bot.db.server_setting.get_single(server_id=server_id, setting="Staff")
    server = bot.get_guild(server_id)
    if server is None:
        return False
    member = server.get_member(user_id)
    if member is None:
        return False

    if set(staff_role_ids) & set(role.id for role in member.roles):
        return True
    return False


@cache
def check_is_trusted_or_staff():
    """Check if the user has a trusted or staff role, as defined in the server settings."""

    async def predicate(ctx: Context[RedstoneSquid]) -> bool:
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