"""Resolve Discord identities and membership into application actors."""

from dataclasses import replace
from typing import TYPE_CHECKING, Any, override
from uuid import UUID

import discord
from discord.ext.commands import Context
from whenever import Instant

import squid_ui.ext.discord as sd
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext, UserActor, current_acting_context, impersonation_use
from squid.core.errors import AuthorizationError
from squid_ui.interactions import ActionMiddleware, ActionProceed, ActionRequest

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

_ACTOR_ATTRIBUTE = "_squid_actor"
"""Memo shared by a prefix command and its parent-group permission checks."""


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


class DiscordActionContext(ActionMiddleware):
    """Use the member who triggered each mounted control or modal submission."""

    @override
    async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
        source = request.event.context
        if not isinstance(source, sd.Request):
            message = "A Discord action requires its live Discord request"
            raise TypeError(message)
        actor = await source.client.actors.resolve(source)
        with ActingContext.direct(actor).scope():
            await proceed()


class DiscordActorResolver:
    """Build actors using one bot's account cache and application-owner rules."""

    def __init__(self, bot: RedstoneSquid) -> None:
        self._bot = bot

    async def request_actor(
        self, request: sd.Request[Any, RedstoneSquid], session_id: UUID | None, operation: str
    ) -> UserActor:
        context = await self.acting_context(request, session_id, operation)
        active = current_acting_context.get()
        if active is not None:
            # The ingress scope restores both bindings when this callback ends.
            if (
                not isinstance(active.operator, UserActor)
                or active.operator.discord_user_id != context.operator.require_discord_user_id()
            ):
                raise AuthorizationError
            current_acting_context.set(replace(context, invocation_id=active.invocation_id))
            impersonation_use.set(context.effective.audit_reference)
        return context.effective.require_user()

    async def acting_context(
        self, request: sd.Request[Any, RedstoneSquid], session_id: UUID | None, operation: str
    ) -> ActingContext:
        """Resolve a private workspace callback against the session it opened with."""
        operator = await self.resolve(request, refresh=True)
        active = current_acting_context.get()
        if (
            active is not None
            and isinstance(active.operator, UserActor)
            and active.operator.discord_user_id == operator.discord_user_id
        ):
            operator_context = replace(active, operator=operator, effective=operator, session_id=None)
        else:
            operator_context = ActingContext.direct(operator)
        if session_id is None:
            return operator_context
        guild = request.guild
        channel = request.channel
        if guild is None or not isinstance(channel, discord.abc.GuildChannel | discord.Thread):
            raise AuthorizationError
        resolved = await self._bot.services.impersonation.use(
            f"discord:{guild.id}:{operator.discord_user_id}", session_id, operation, context=operator_context
        )
        account = resolved.target
        if resolved.discord_id is None:
            raise AuthorizationError
        try:
            member = await guild.fetch_member(resolved.discord_id)
            permissions = channel.permissions_for(member)
            if not permissions.view_channel:
                raise AuthorizationError
            if isinstance(channel, discord.Thread) and channel.is_private() and not permissions.manage_threads:
                await channel.fetch_member(member.id)
        except (discord.NotFound, discord.Forbidden) as error:
            raise AuthorizationError from error
        effective = replace(await self.resolve(member, account_id=account.id), audit_use_id=resolved.use_id)
        return replace(operator_context, effective=effective, session_id=session_id)

    async def resolve(
        self,
        source: sd.Request[Any, Any] | Context[Any] | discord.Member,
        *,
        refresh: bool = False,
        account_id: int | None = None,
    ) -> UserActor:
        """Resolve a request, command context, or already-fetched member.

        Normal checks use the supplied membership and cached account lookup.
        Command contexts also memoize the actor across their parent-group checks.
        With `refresh=True`, fetch current guild membership and bypass both
        caches. Discord failures propagate; a departed member is denied. Callers
        that already resolved the user's identity may supply its `account_id`.
        """
        if isinstance(source, discord.Member):
            user, guild = source, source.guild
            memoize = False
        elif isinstance(source, sd.Request):
            user, guild = source.user, source.guild
            memoize = False
        else:
            user, guild = source.author, source.guild
            memoize = not refresh and account_id is None
            if memoize and isinstance(memoized := getattr(source, _ACTOR_ATTRIBUTE, None), UserActor):
                return memoized

        member = user if isinstance(user, discord.Member) and guild is not None else None
        if guild is not None and member is not None and member.guild.id != guild.id:
            raise AuthorizationError
        if refresh:
            member = None
            if guild is not None:
                try:
                    member = await guild.fetch_member(user.id)
                except discord.NotFound as error:
                    raise AuthorizationError from error
            account = await self._bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
            account_id = None if account is None else account.id
        elif account_id is None:
            account_id = await self._bot.account_ids.resolve(self._bot.services.accounts, user.id)

        actor = UserActor(
            discord_user_id=user.id,
            account_id=account_id,
            discord_role_ids=frozenset(role.id for role in member.roles) if member is not None else frozenset(),
            guild_id=None if guild is None else guild.id,
            is_bot_owner=await self._bot.is_owner(user),
            discord_guild_admin=member is not None and has_manage_server(member),
        )
        if memoize:
            setattr(source, _ACTOR_ATTRIBUTE, actor)
        return actor
