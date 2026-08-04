"""Permission checking utilities for the bot."""

from functools import cache
from typing import TYPE_CHECKING

import discord
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check

if TYPE_CHECKING:
    import squid.bot.app


class GlobalAdministratorRequired(CheckFailure):
    """Raised when a command requires bot-wide administrative access."""


class ServerAdministratorRequired(CheckFailure):
    """Raised when a command requires administrative access in the current guild."""


class TrustedOrGlobalAdministratorRequired(CheckFailure):
    """Raised when a command requires a Trusted role or bot-wide administrative access."""


class HomeServerTrustedOrGlobalAdministratorRequired(CheckFailure):
    """Raised when home-server Trusted access or bot-wide administrative access is required."""


async def is_global_admin(bot: "squid.bot.app.RedstoneSquid", user_id: int) -> bool:
    """Return whether a user is the Discord application owner or a persisted global administrator."""
    if await bot.is_owner(discord.Object(id=user_id)):  # type: ignore[arg-type]
        return True
    return await bot.services.authorization.is_global_administrator(user_id)


@cache
def check_is_global_admin():
    """Require the bot owner or a persisted global administrator."""

    async def predicate(ctx: Context["squid.bot.app.RedstoneSquid"]) -> bool:
        if await ctx.bot.is_owner(ctx.author) or await ctx.bot.services.authorization.is_global_administrator(
            ctx.author.id
        ):
            return True
        raise GlobalAdministratorRequired()

    return check(predicate)


def _has_server_admin_permission(member: discord.Member) -> bool:
    permissions = member.guild_permissions
    return member.id == member.guild.owner_id or permissions.administrator or permissions.manage_guild


async def is_server_admin(bot: "squid.bot.app.RedstoneSquid", server_id: int | None, user_id: int) -> bool:
    """Return whether a user administers the bot globally or the specified Discord guild."""
    if await is_global_admin(bot, user_id):
        return True
    if server_id is None:
        return False
    guild = bot.get_guild(server_id)
    member = guild.get_member(user_id) if guild is not None else None
    return member is not None and _has_server_admin_permission(member)


@cache
def check_is_server_admin():
    """Require guild ownership, Manage Server, Administrator, or a higher bot permission."""

    async def predicate(ctx: Context["squid.bot.app.RedstoneSquid"]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        if await is_global_admin(ctx.bot, ctx.author.id):
            return True
        if isinstance(ctx.author, discord.Member) and _has_server_admin_permission(ctx.author):
            return True
        raise ServerAdministratorRequired()

    return check(predicate)


async def is_trusted_or_global_admin(bot: "squid.bot.app.RedstoneSquid", server_id: int | None, user_id: int) -> bool:
    """Return whether a user is a global administrator or has a configured Trusted role."""
    if await is_global_admin(bot, user_id):
        return True
    if server_id is None:
        return False
    guild = bot.get_guild(server_id)
    member = guild.get_member(user_id) if guild is not None else None
    if member is None:
        return False
    trusted_role_ids = await bot.services.settings.get(server_id, "Trusted")
    return any(role.id in trusted_role_ids for role in member.roles)


@cache
def check_is_trusted_or_global_admin():
    """Require a configured Trusted role or global administrative access."""

    async def predicate(ctx: Context["squid.bot.app.RedstoneSquid"]) -> bool:
        if ctx.guild is None:
            if await is_global_admin(ctx.bot, ctx.author.id):
                return True
            raise NoPrivateMessage()
        if await is_trusted_or_global_admin(ctx.bot, ctx.guild.id, ctx.author.id):
            return True
        raise TrustedOrGlobalAdministratorRequired()

    return check(predicate)


@cache
def check_is_home_server_trusted_or_global_admin():
    """Allow global administrators anywhere and Trusted roles only in the configured home guild."""

    async def predicate(ctx: Context["squid.bot.app.RedstoneSquid"]) -> bool:
        if await is_global_admin(ctx.bot, ctx.author.id):
            return True
        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.bot.owner_server_id is not None and ctx.guild.id != ctx.bot.owner_server_id:
            raise HomeServerTrustedOrGlobalAdministratorRequired()
        if await is_trusted_or_global_admin(ctx.bot, ctx.guild.id, ctx.author.id):
            return True
        raise HomeServerTrustedOrGlobalAdministratorRequired()

    return check(predicate)


def is_owner_server(bot: "squid.bot.app.RedstoneSquid", server_id: int) -> bool:
    """Return whether a server hosts features scoped to the bot's home community."""
    return server_id == bot.owner_server_id


@cache
def check_is_home_server():
    """Require the guild configured for home-community-specific features."""

    async def predicate(ctx: Context["squid.bot.app.RedstoneSquid"]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.bot.owner_server_id is None or ctx.guild.id == ctx.bot.owner_server_id:
            return True
        msg = "This feature is only available in the bot's home server."
        raise CheckFailure(msg)

    return check(predicate)
