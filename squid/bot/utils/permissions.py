"""Permission checks for commands and interactions.

Commands declare the permission *nodes* they need with `requires(...)`; the engine in
`squid.permissions` decides. `check_is_home_server` is the one non-node check here, and it
asks where a command runs rather than who runs it.
"""

from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Any, Literal, cast

import discord
from discord import app_commands
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check
from whenever import Instant

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.permissions.application import PermissionService
from squid.permissions.domain import CATALOGUE, Decision, PermissionNode, Reason, Subject

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    import squid.bot.app

type CheckMode = Literal["all", "any"]

SUBJECT_ATTRIBUTE = "_squid_permission_subject"
"""Context attribute memoizing the resolved subject for one invocation.

Checks run before `before_invoke`, and a command in a group runs the group's checks too, so the
memo lives on the context object rather than in a hook.
"""


class PermissionNodeRequired(CheckFailure):
    """Raised by `requires(...)` and `enforce` when the caller does not hold the declared nodes.

    Carries the node names rather than a rendered message, so the error presenter can translate
    each catalogue description and distinguish a missing node from an explicit `forbid`.
    """

    def __init__(self, nodes: tuple[str, ...], *, mode: CheckMode = "all", forbidden: bool = False) -> None:
        self.nodes = nodes
        self.mode = mode
        self.forbidden = forbidden
        super().__init__(f"Missing permission node: {', '.join(nodes)}")


class AccountIdCache:
    """Discord id to account id, read-only so a permission check never writes a row for a stranger.

    Absences are cached too, but expire faster than hits, since linking an account is the event
    that invalidates one. `squid.bot.utils.accounts.account_id_for` is the get-or-create
    counterpart for command paths that may create.
    """

    def __init__(self, *, ttl_seconds: float = 300, miss_ttl_seconds: float = 30, max_entries: int = 4096) -> None:
        self._entries: dict[int, tuple[int | None, Instant]] = {}
        self._ttl_seconds = ttl_seconds
        self._miss_ttl_seconds = miss_ttl_seconds
        self._max_entries = max_entries

    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        """The account id behind a Discord id, or None when there is no account.

        Reaching `max_entries` drops every entry rather than evicting the oldest.
        """
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
    """True for the guild owner and for anyone holding `administrator` or `manage_guild`."""
    permissions = member.guild_permissions
    return member.id == member.guild.owner_id or permissions.administrator or permissions.manage_guild


async def build_subject(
    bot: squid.bot.app.RedstoneSquid,
    user: discord.User | discord.Member | discord.abc.User,
    guild_id: int | None,
) -> Subject:
    """Describe a caller for the permission engine.

    Role membership is read straight off the member: `member.roles` is gateway-fresh, and caching
    it would add staleness the gateway does not have.
    """
    member = user if isinstance(user, discord.Member) else None
    return Subject(
        account_id=await bot.account_ids.resolve(bot.services.accounts, user.id),
        discord_role_ids=frozenset(role.id for role in member.roles) if member is not None else frozenset(),
        guild_id=guild_id,
        is_bot_owner=await bot.is_owner(user),  # pyright: ignore[reportArgumentType]
        discord_guild_admin=member is not None and has_manage_server(member),
    )


async def subject_for(ctx: Context[squid.bot.app.RedstoneSquid]) -> Subject:
    """The caller's subject, resolved once per invocation."""
    memoized = getattr(ctx, SUBJECT_ATTRIBUTE, None)
    if isinstance(memoized, Subject):
        return memoized
    subject = await build_subject(ctx.bot, ctx.author, ctx.guild.id if ctx.guild is not None else None)
    setattr(ctx, SUBJECT_ATTRIBUTE, subject)
    return subject


type Caller = discord.Interaction[squid.bot.app.RedstoneSquid] | sd.Request[Any]
"""Whoever is asking: a request, or the bare interaction a surface not yet ported still holds."""


async def subject_for_interaction(caller: Caller) -> Subject:
    """The subject behind an interaction, resolved fresh: unlike `subject_for`, nothing memoizes it."""
    if isinstance(caller, sd.Request):
        guild_id = None if caller.guild is None else caller.guild.id
        return await build_subject(cast("squid.bot.app.RedstoneSquid", caller.client), caller.user, guild_id)
    return await build_subject(caller.client, caller.user, caller.guild_id)


def _permissions_of(caller: Caller) -> PermissionService:
    return cast("squid.bot.app.RedstoneSquid", caller.client).services.permissions


async def allows(caller: Caller, node: PermissionNode | str) -> bool:
    """Whether the user behind `caller` holds `node`."""
    subject = await subject_for_interaction(caller)
    return await _permissions_of(caller).allows(subject, node)


async def enforce(
    caller: Caller,
    *nodes: PermissionNode | str,
    mode: CheckMode = "all",
) -> None:
    """Deny an interaction the way `requires(...)` denies a command.

    For context menus and component callbacks, which cannot carry a `commands.check`. Raising what
    the decorator raises keeps one presenter rendering both refusals, `forbid` explanation included.

    Raises:
        PermissionNodeRequired: The caller does not hold the nodes.
        UnknownPermissionNodeError: A node was named by a string the catalogue does not define.
    """
    resolved = tuple(CATALOGUE[node] if isinstance(node, str) else node for node in nodes)
    subject = await subject_for_interaction(caller)
    decisions = await _permissions_of(caller).decisions(subject, resolved)
    if _satisfied(decisions, mode):
        return
    raise PermissionNodeRequired(
        tuple(node.name for node in resolved),
        mode=mode,
        forbidden=any(decision.reason is Reason.FORBIDDEN for decision in decisions),
    )


def requires(
    *nodes: PermissionNode | str,
    mode: CheckMode = "all",
    guild_only: bool = False,
) -> Check[Context[squid.bot.app.RedstoneSquid]]:
    """Require permission nodes, decided by the permission engine.

    `mode="any"` passes when the caller holds one of the nodes, for a command reachable by more than
    one route. The check raises `PermissionNodeRequired` on refusal and `NoPrivateMessage` when
    `guild_only` and the command runs in a DM, so a denial is never a bare "check failed".

    Raises:
        ValueError: No nodes were given.
        UnknownPermissionNodeError: A node name is not in the catalogue. Raised at decoration time,
            so a typo fails at import rather than denying a real user at runtime.
    """
    if not nodes:
        msg = "requires() needs at least one permission node."
        raise ValueError(msg)
    resolved = tuple(CATALOGUE[node] if isinstance(node, str) else node for node in nodes)

    async def predicate(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
        if guild_only and ctx.guild is None:
            raise NoPrivateMessage()
        subject = await subject_for(ctx)
        decisions = await ctx.bot.services.permissions.decisions(subject, resolved)
        if _satisfied(decisions, mode):
            return True
        raise PermissionNodeRequired(
            tuple(node.name for node in resolved),
            mode=mode,
            forbidden=any(decision.reason is Reason.FORBIDDEN for decision in decisions),
        )

    # Stamped on the predicate so the taxonomy test reads a command's real contract instead of
    # guessing it from a check's name.
    predicate.__squid_nodes__ = tuple(node.name for node in resolved)  # pyrefly: ignore[missing-attribute]
    predicate.__squid_mode__ = mode  # pyrefly: ignore[missing-attribute]
    return check(predicate)


def hide_unless[CommandT](**permissions: bool) -> Callable[[CommandT], CommandT]:
    """Keep a command out of the picker of viewers lacking these Discord permissions.

    Visibility only: guild admins can override it per command, so `requires(...)`
    stays the gate. Discord reads the field on top-level commands only and ignores
    it on subcommands.
    """
    return app_commands.default_permissions(**permissions)


def _satisfied(decisions: tuple[Decision, ...], mode: CheckMode) -> bool:
    return any(d.allowed for d in decisions) if mode == "any" else all(d.allowed for d in decisions)


@cache
def check_is_home_server():
    """Require the guild configured for home-community-specific features.

    Feature availability, not authorization: it asks where a command runs, never who runs it, so it
    is applied alongside `requires(...)` rather than instead of it. The check raises
    `NoPrivateMessage` in a DM and `CheckFailure` in any other guild.
    """

    async def predicate(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.bot.owner_server_id is None or ctx.guild.id == ctx.bot.owner_server_id:
            return True
        msg = "This feature is only available in the bot's home server."
        raise CheckFailure(msg)

    return check(predicate)
