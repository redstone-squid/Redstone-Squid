"""Translate Discord callers into permission subjects and enforce declared nodes.

requires supplies checks for discord.py commands; enforce performs the same
check from a command body, component callback, or context-menu handler. Grants carries
a viewer's permissions into a screen. The home-server check separately limits where
community features are available.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache, wraps
from typing import TYPE_CHECKING, Any, Concatenate, Protocol, Self

from discord import app_commands
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.core.i18n import tr
from squid.core.permissions.domain import CATALOGUE, CheckMode, PermissionNode
from squid.core.permissions.errors import PermissionDeniedError, require_check
from squid_ui.content.text import TextLike

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    import squid.bot.app


class PermissionNodeRequired(PermissionDeniedError, CheckFailure):
    """Discord command-check form of the shared ACL denial."""


async def allows(caller: sd.Request[Any, squid.bot.app.RedstoneSquid], node: PermissionNode | str) -> bool:
    """Whether the user behind `caller` holds `node`."""
    subject = await caller.client.actors.resolve(caller)
    return (await caller.client.services.permissions.check(subject, node)).allowed


@dataclass(frozen=True, slots=True)
class Grants:
    """The permission nodes a viewer held when a screen opened, and a way to recheck them.

    Screens test membership (`node in grants`) to choose which controls to show, and recheck
    before acting with `requires_grant` or `confirm`, because permissions can change while a
    screen stays open. Services still enforce their own checks; the recheck only turns a refusal
    into a short notice.
    """

    held: frozenset[PermissionNode]
    check: Callable[[PermissionNode], Awaitable[bool]]

    @classmethod
    async def load(cls, caller: sd.Request[Any, squid.bot.app.RedstoneSquid], *nodes: PermissionNode) -> Self:
        """Check `nodes` for the user behind `caller`, and recheck through the same request later."""

        async def check(node: PermissionNode) -> bool:
            return await allows(caller, node)

        return cls(frozenset([node for node in nodes if await check(node)]), check)

    def __contains__(self, node: PermissionNode) -> bool:
        return node in self.held

    async def allows(self, *nodes: PermissionNode) -> bool:
        """Whether the viewer currently holds any of `nodes`."""
        for node in nodes:
            if await self.check(node):
                return True
        return False

    async def confirm(self, event: sl.ActionEvent[Any], *nodes: PermissionNode, denied: TextLike | None = None) -> bool:
        """Like `allows`, but tell the viewer when they no longer hold any of `nodes`.

        `denied` replaces the generic refusal, for a screen that can say what was refused.
        """
        if await self.allows(*nodes):
            return True
        await event.notice(denied or tr(t"permission-revoked = You are no longer allowed to do this."))
        return False


class _HasGrants(Protocol):
    @property
    def grants(self) -> Grants: ...


def requires_grant[OwnerT: _HasGrants, **P](
    *nodes: PermissionNode, denied: TextLike | None = None
) -> Callable[[Callable[Concatenate[OwnerT, P], Awaitable[None]]], Callable[Concatenate[OwnerT, P], Awaitable[None]]]:
    """Run a screen's event handler only while the viewer still holds one of `nodes`.

    The handler's owner exposes `grants`, and one of the handler's arguments is the action
    event; otherwise the handler is skipped after `Grants.confirm` tells the viewer why.
    Handlers that choose the node at runtime call `confirm` themselves.
    """

    def decorate(
        handler: Callable[Concatenate[OwnerT, P], Awaitable[None]],
    ) -> Callable[Concatenate[OwnerT, P], Awaitable[None]]:
        @wraps(handler)
        async def guarded(owner: OwnerT, *args: P.args, **kwargs: P.kwargs) -> None:
            event = next(arg for arg in (*args, *kwargs.values()) if isinstance(arg, sl.ActionEvent))
            if await owner.grants.confirm(event, *nodes, denied=denied):
                await handler(owner, *args, **kwargs)

        return guarded

    return decorate


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
