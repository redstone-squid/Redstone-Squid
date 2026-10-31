"""Permission checking utilities for the bot.

Commands declare the permission *nodes* they need with `requires(...)`, and the
engine in `squid.permissions` decides. Nothing here knows about tiers any more:
`check_is_home_server` is the one remaining non-node check, and it asks where a
command runs rather than who is running it.
"""

from functools import cache
from typing import TYPE_CHECKING, Literal

import discord
from discord.ext.commands import CheckFailure, Context, NoPrivateMessage, check
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.permissions.domain import CATALOGUE, Decision, PermissionNode, Reason, Subject

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    import squid.bot.app

type CheckMode = Literal["all", "any"]

SUBJECT_ATTRIBUTE = "_squid_permission_subject"
"""Where a resolved subject is memoized for the rest of one invocation.

Checks run *before* `before_invoke`, and a command in a group runs the group's
checks too, so the memo lives on the context object rather than in a hook.
"""


class PermissionNodeRequired(CheckFailure):
    """Raised when the caller does not hold the nodes a command declares.

    Carries the nodes rather than a rendered message so the error presenter can
    translate the description and say something specific about a `forbid`.
    """

    def __init__(self, nodes: tuple[str, ...], *, mode: CheckMode = "all", forbidden: bool = False) -> None:
        self.nodes = nodes
        self.mode = mode
        self.forbidden = forbidden
        super().__init__(f"Missing permission node: {', '.join(nodes)}")


class AccountIdCache:
    """Discord id to account id, held briefly.

    A permission check must never create an account row — that would write one
    for every unauthenticated caller — so this only ever reads, and caches the
    absence of an account too. Misses expire faster than hits because linking an
    account is exactly the event that invalidates one.

    This is why the read goes through `get_account_by_identity` and not
    `get_or_create_identity`: observing a snowflake in a permission check is not
    evidence anybody asked us to remember them. `account_id_for` is the
    get-or-create counterpart, for the command paths that did.
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
    """Whether a member holds what Discord calls administrative control of a guild."""
    permissions = member.guild_permissions
    return member.id == member.guild.owner_id or permissions.administrator or permissions.manage_guild


async def build_subject(
    bot: squid.bot.app.RedstoneSquid,
    user: discord.User | discord.Member | discord.abc.User,
    guild_id: int | None,
) -> Subject:
    """Describe a caller for the permission engine.

    Discord role membership is not cached here: `member.roles` is gateway-fresh
    already, and caching it would add staleness the gateway does not have.
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


async def subject_for_interaction(interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> Subject:
    """The subject behind a component or modal interaction."""
    return await build_subject(interaction.client, interaction.user, interaction.guild_id)


async def allows(
    interaction: discord.Interaction[squid.bot.app.RedstoneSquid],
    node: PermissionNode | str,
) -> bool:
    """Whether the user behind an interaction holds `node`."""
    subject = await subject_for_interaction(interaction)
    return await interaction.client.services.permissions.allows(subject, node)


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
        subject = await subject_for(ctx)
        decisions = await ctx.bot.services.permissions.decisions(subject, resolved)
        if _satisfied(decisions, mode):
            return True
        raise PermissionNodeRequired(
            tuple(node.name for node in resolved),
            mode=mode,
            forbidden=any(decision.reason is Reason.FORBIDDEN for decision in decisions),
        )

    # Stamped on the predicate so the taxonomy test can read a command's real
    # contract instead of guessing it from a check's name.
    predicate.__squid_nodes__ = tuple(node.name for node in resolved)  # pyrefly: ignore[missing-attribute]
    predicate.__squid_mode__ = mode  # pyrefly: ignore[missing-attribute]
    return check(predicate)


def _satisfied(decisions: tuple[Decision, ...], mode: CheckMode) -> bool:
    return any(d.allowed for d in decisions) if mode == "any" else all(d.allowed for d in decisions)


@cache
def check_is_home_server():
    """Require the guild configured for home-community-specific features.

    Feature availability, not authorization: it asks *where* a command is being
    run, never *who* is running it. Conflating the two is what produced the four
    overlapping tiers this module is replacing, so it stays a separate check
    applied alongside `requires(...)`.
    """

    async def predicate(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
        if ctx.guild is None:
            raise NoPrivateMessage()
        if ctx.bot.owner_server_id is None or ctx.guild.id == ctx.bot.owner_server_id:
            return True
        msg = "This feature is only available in the bot's home server."
        raise CheckFailure(msg)

    return check(predicate)
