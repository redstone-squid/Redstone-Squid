"""Translate Discord callers into permission subjects and enforce declared nodes.

requires supplies checks for discord.py commands; enforce performs the same
check from a command body, component callback, or context-menu handler. The
home-server check separately limits where community features are available.
"""

from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Any

from discord import app_commands
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check

import squid_ui_discord as sd
from squid.permissions.domain import CATALOGUE, CheckMode, PermissionNode
from squid.permissions.errors import PermissionDeniedError, require_check

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    import squid.bot.app


class PermissionNodeRequired(PermissionDeniedError, CheckFailure):
    """Discord command-check form of the shared ACL denial."""


async def allows(caller: sd.Request[Any, squid.bot.app.RedstoneSquid], node: PermissionNode | str) -> bool:
    """Whether the user behind `caller` holds `node`."""
    subject = await caller.client.actors.resolve(caller)
    return (await caller.client.services.permissions.check(subject, node)).allowed


async def enforce(
    caller: sd.Request[Any, squid.bot.app.RedstoneSquid],
    *nodes: PermissionNode | str,
    mode: CheckMode = "all",
) -> None:
    """Check an interaction's permission nodes and raise the shared command-check error.

    The common error presenter renders the same refusal for commands and component
    actions, including a distinct explanation for forbid rules.

    Raises:
        PermissionNodeRequired: The caller does not satisfy the requested mode.
    """
    resolved = tuple(CATALOGUE[node] if isinstance(node, str) else node for node in nodes)
    subject = await caller.client.actors.resolve(caller)
    result = await caller.client.services.permissions.check(subject, *resolved, mode=mode)
    require_check(result, error_type=PermissionNodeRequired)


def requires(
    *nodes: PermissionNode | str,
    mode: CheckMode = "all",
    guild_only: bool = False,
) -> Check[Context[squid.bot.app.RedstoneSquid]]:
    """Require permission nodes, decided by the permission engine.

    `mode="any"` passes when the caller holds one of the nodes, for a command
    reachable by more than one route. Node names are validated against the
    catalogue at import time, so a typo fails at startup rather than denying a
    real user at runtime.
    """
    if not nodes:
        msg = "requires() needs at least one permission node."
        raise ValueError(msg)
    resolved = tuple(CATALOGUE[node] if isinstance(node, str) else node for node in nodes)

    async def predicate(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
        if guild_only and ctx.guild is None:
            raise NoPrivateMessage()
        subject = await ctx.bot.actors.resolve(ctx)
        result = await ctx.bot.services.permissions.check(subject, *resolved, mode=mode)
        require_check(result, error_type=PermissionNodeRequired)
        return True

    # Stamped on the predicate so the taxonomy test can read a command's real
    # contract instead of guessing it from a check's name. Both checkers type a function object
    # as FunctionType, which has no room for an extra attribute even though Python does.
    predicate.__squid_nodes__ = tuple(node.name for node in resolved)  # pyrefly: ignore[missing-attribute]  # pyright: ignore[reportFunctionMemberAccess]
    predicate.__squid_mode__ = mode  # pyrefly: ignore[missing-attribute]  # pyright: ignore[reportFunctionMemberAccess]
    return check(predicate)


def hide_unless[CommandT](**permissions: bool) -> Callable[[CommandT], CommandT]:
    """Keep a command out of the picker of viewers lacking these Discord permissions.

    Visibility only: guild admins can override it per command, so `requires(...)`
    stays the gate. Discord reads the field on top-level commands only and ignores
    it on subcommands.
    """
    return app_commands.default_permissions(**permissions)


@cache
def check_is_home_server():
    """Require the configured home guild, or any guild if none is configured.

    This limits feature availability and does not check the caller's authority.
    Combine it with `requires` when both restrictions apply. DMs are rejected.
    """

    async def predicate(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.bot.owner_server_id is None or ctx.guild.id == ctx.bot.owner_server_id:
            return True
        msg = "This feature is only available in the bot's home server."
        raise CheckFailure(msg)

    return check(predicate)
