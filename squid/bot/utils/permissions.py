"""Translate Discord callers into permission subjects and enforce declared nodes.

requires supplies checks for discord.py commands; enforce performs the same
check from a command body, component callback, or context-menu handler. The
home-server check separately limits where community features are available.
"""

from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Any, cast

import discord
from discord import app_commands
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check
from whenever import Instant

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.permissions.application import PermissionService
from squid.permissions.domain import CATALOGUE, PermissionNode
from squid.permissions.errors import CheckMode, PermissionDeniedError, require_decisions

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    import squid.bot.app

ACTOR_ATTRIBUTE = "_squid_actor"
"""Where a resolved subject is memoized for the rest of one invocation.

Checks run *before* `before_invoke`, and a command in a group runs the group's
checks too, so the memo lives on the context object rather than in a hook.
"""


class PermissionNodeRequired(PermissionDeniedError, CheckFailure):
    """Discord command-check form of the shared ACL denial."""


class AccountIdCache:
    """Cache Discord-to-account lookups without creating accounts.

    Known accounts live for `ttl_seconds`; missing accounts use the shorter
    `miss_ttl_seconds` so new links become visible sooner. Reaching `max_entries`
    clears the whole cache rather than evicting one entry. `forget` invalidates
    a known identity change immediately.
    """

    def __init__(self, *, ttl_seconds: float = 300, miss_ttl_seconds: float = 30, max_entries: int = 4096) -> None:
        self._entries: dict[int, tuple[int | None, Instant]] = {}
        self._ttl_seconds = ttl_seconds
        self._miss_ttl_seconds = miss_ttl_seconds
        self._max_entries = max_entries

    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        """The account id behind a Discord id, or None when there is no account."""
        now = Instant.now()
        cached = self._entries.get(discord_id)
        if cached is not None and cached[1] > now:
            return cached[0]

        account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(discord_id))
        account_id = account.id if account is not None else None
        ttl = self._ttl_seconds if account_id is not None else self._miss_ttl_seconds
        if len(self._entries) >= self._max_entries:
            self._entries.clear()
        self._entries[discord_id] = (account_id, now.add(seconds=ttl))
        return account_id

    def forget(self, discord_id: int) -> None:
        """Drop one entry, for when an account has just been linked."""
        self._entries.pop(discord_id, None)


def has_manage_server(member: discord.Member) -> bool:
    """Return whether the member owns the guild or has Administrator or Manage Server."""
    permissions = member.guild_permissions
    return member.id == member.guild.owner_id or permissions.administrator or permissions.manage_guild


async def build_actor(
    bot: squid.bot.app.RedstoneSquid,
    user: discord.User | discord.Member | discord.abc.User,
    guild_id: int | None,
) -> Actor:
    """Build a permission subject from the supplied Discord user and guild context.

    Member roles come from the supplied object without a REST refresh. Account
    identity uses the shared lookup cache; the owner check is delegated to the bot.
    """
    member = user if isinstance(user, discord.Member) else None
    return Actor(
        discord_user_id=user.id,
        account_id=await bot.account_ids.resolve(bot.services.accounts, user.id),
        discord_role_ids=frozenset(role.id for role in member.roles) if member is not None else frozenset(),
        guild_id=guild_id,
        is_bot_owner=await bot.is_owner(user),  # pyright: ignore[reportArgumentType]
        discord_guild_admin=member is not None and has_manage_server(member),
    )


async def actor_for(ctx: Context[squid.bot.app.RedstoneSquid]) -> Actor:
    """The caller's subject, resolved once per invocation."""
    memoized = getattr(ctx, ACTOR_ATTRIBUTE, None)
    if isinstance(memoized, Actor):
        return memoized
    subject = await build_actor(ctx.bot, ctx.author, ctx.guild.id if ctx.guild is not None else None)
    setattr(ctx, ACTOR_ATTRIBUTE, subject)
    return subject


async def actor_for_interaction(caller: sd.Request[Any]) -> Actor:
    """Build the permission subject for a command, component, or modal request."""
    guild_id = None if caller.guild is None else caller.guild.id
    return await build_actor(cast("squid.bot.app.RedstoneSquid", caller.client), caller.user, guild_id)


async def current_edit_actor(caller: sd.Request[Any]) -> Actor:
    """Refresh identity and guild membership for a long-lived editor action.

    An editor can outlive a role change or account merge. Its opening request is
    sufficient to identify the user, but not to authorize a later save or undo.
    Discord failures abort the edit; they must not fall back to stale roles.
    """
    bot: squid.bot.app.RedstoneSquid = cast(Any, caller.client)
    user = caller.user
    guild = caller.guild
    member: discord.Member | None = None
    if guild is not None:
        try:
            member = await guild.fetch_member(user.id)
        except discord.NotFound as error:
            raise AuthorizationError from error
    account = await bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    return Actor(
        account_id=None if account is None else account.id,
        discord_role_ids=frozenset(role.id for role in member.roles) if member is not None else frozenset(),
        guild_id=None if guild is None else guild.id,
        is_bot_owner=await bot.is_owner(user),
        discord_guild_admin=member is not None and has_manage_server(member),
        discord_user_id=user.id,
    )


def _permissions_of(caller: sd.Request[Any]) -> PermissionService:
    return cast("squid.bot.app.RedstoneSquid", caller.client).services.permissions


async def allows(caller: sd.Request[Any], node: PermissionNode | str) -> bool:
    """Whether the user behind `caller` holds `node`."""
    subject = await actor_for_interaction(caller)
    return await _permissions_of(caller).allows(subject, node)


async def enforce(
    caller: sd.Request[Any],
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
    subject = await actor_for_interaction(caller)
    decisions = await _permissions_of(caller).decisions(subject, resolved)
    require_decisions(decisions, mode=mode, error_type=PermissionNodeRequired)


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
        subject = await actor_for(ctx)
        decisions = await ctx.bot.services.permissions.decisions(subject, resolved)
        require_decisions(decisions, mode=mode, error_type=PermissionNodeRequired)
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
