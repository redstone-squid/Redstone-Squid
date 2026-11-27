"""Actor resolution preserves owner authority while refreshing mutable identity."""

from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import discord
import pytest
from discord.ext.commands import Context

import squid_ui.ext.discord as sd
from squid.bot.actors import AccountIdCache, DiscordActorResolver
from squid.bot.app import RedstoneSquid
from squid.core.accounts.domain import PrivacyNoticeReceipt
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.actors import ActingContext, UserActor, current_acting_context, impersonation_use
from squid.core.auth.impersonation import AuthorizedImpersonation
from squid.core.errors import AuthorizationError


@pytest.fixture
def bot() -> Mock:
    client = Mock(spec=RedstoneSquid)
    client.services = Mock()
    client.services.accounts.get_account_by_identity = AsyncMock(return_value=Account(id=42))
    client.account_ids = AccountIdCache()
    client.is_owner = AsyncMock(return_value=True)
    client.actors = DiscordActorResolver(cast(RedstoneSquid, client))
    return client


@pytest.fixture
def member() -> Mock:
    guild = Mock(spec=discord.Guild, id=100, owner_id=9)
    user = Mock(spec=discord.Member, id=7, guild=guild)
    user.roles = [discord.Object(30)]
    user.guild_permissions = discord.Permissions(manage_guild=True)
    guild.fetch_member = AsyncMock(return_value=user)
    return user


@pytest.mark.parametrize("kind", ["request", "context", "member"])
async def test_sources_share_actor_construction(bot: Mock, member: Mock, kind: str) -> None:
    if kind == "request":
        source = Mock(spec=sd.Request, user=member, guild=member.guild)
    elif kind == "context":
        source = Mock(spec=Context, author=member, guild=member.guild)
    else:
        source = member

    actor = await bot.actors.resolve(source)

    assert actor.account_id == 42
    assert actor.discord_user_id == 7
    assert actor.guild_id == 100
    assert actor.discord_role_ids == frozenset({30})
    assert actor.discord_guild_admin
    assert actor.is_bot_owner
    member.guild.fetch_member.assert_not_awaited()
    bot.is_owner.assert_awaited_once_with(member)


async def test_refresh_replaces_membership_and_account_but_preserves_owner(bot: Mock, member: Mock) -> None:
    request = Mock(spec=sd.Request, user=member, guild=member.guild)
    previous = await bot.actors.resolve(request)
    current = Mock(spec=discord.Member, id=7, guild=member.guild)
    current.roles = []
    current.guild_permissions = discord.Permissions.none()
    member.guild.fetch_member.return_value = current
    bot.services.accounts.get_account_by_identity.return_value = Account(id=77)

    refreshed = await bot.actors.resolve(request, refresh=True)

    assert previous.account_id == 42
    assert refreshed.account_id == 77
    assert refreshed.discord_role_ids == frozenset()
    assert not refreshed.discord_guild_admin
    assert refreshed.is_bot_owner
    assert refreshed.discord_user_id == 7
    member.guild.fetch_member.assert_awaited_once_with(7)
    assert bot.services.accounts.get_account_by_identity.await_count == 2


async def test_refresh_bypasses_command_memo_without_replacing_it(bot: Mock, member: Mock) -> None:
    context = Mock(spec=Context, author=member, guild=member.guild)
    first = await bot.actors.resolve(context)
    assert await bot.actors.resolve(context) is first
    bot.services.accounts.get_account_by_identity.return_value = None

    refreshed = await bot.actors.resolve(context, refresh=True)

    assert refreshed.account_id is None
    assert await bot.actors.resolve(context) is first
    assert bot.is_owner.await_count == 2
    assert bot.services.accounts.get_account_by_identity.await_count == 2


@pytest.mark.parametrize("failure", [discord.NotFound, discord.Forbidden, discord.HTTPException])
async def test_failed_refresh_never_falls_back_to_cached_membership(
    bot: Mock, member: Mock, failure: type[discord.HTTPException]
) -> None:
    request = Mock(spec=sd.Request, user=member, guild=member.guild)
    await bot.actors.resolve(request)
    error = failure(Mock(status=404, reason="unavailable"), "unavailable")
    member.guild.fetch_member.side_effect = error

    expected = AuthorizationError if failure is discord.NotFound else failure
    with pytest.raises(expected):
        await bot.actors.resolve(request, refresh=True)
    assert bot.services.accounts.get_account_by_identity.await_count == 1


async def test_refresh_in_a_dm_preserves_owner_and_unlinked_identity(bot: Mock) -> None:
    user = Mock(spec=discord.User, id=7)
    request = Mock(spec=sd.Request, user=user, guild=None)
    bot.services.accounts.get_account_by_identity.return_value = None

    actor = await bot.actors.resolve(request, refresh=True)

    assert actor.account_id is None
    assert actor.guild_id is None
    assert actor.discord_role_ids == frozenset()
    assert not actor.discord_guild_admin
    assert actor.is_bot_owner


@pytest.mark.parametrize("kind", ["request", "context"])
async def test_foreign_guild_membership_is_rejected(bot: Mock, member: Mock, kind: str) -> None:
    guild = Mock(spec=discord.Guild, id=200)
    source = (
        Mock(spec=sd.Request, user=member, guild=guild)
        if kind == "request"
        else Mock(spec=Context, author=member, guild=guild)
    )
    with pytest.raises(AuthorizationError):
        await bot.actors.resolve(source)
    bot.services.accounts.get_account_by_identity.assert_not_awaited()


async def test_dm_discards_carried_membership_but_preserves_owner(bot: Mock, member: Mock) -> None:
    source = Mock(spec=sd.Request, user=member, guild=None)
    actor = await bot.actors.resolve(source)
    assert actor.guild_id is None
    assert not actor.discord_role_ids
    assert not actor.discord_guild_admin
    assert actor.is_bot_owner


async def test_debugger_uses_target_membership_for_every_callback(bot: Mock, member: Mock) -> None:
    target = Mock(spec=discord.Member, id=8, guild=member.guild)
    target.guild_permissions = discord.Permissions.none()
    target.roles = [discord.Object(55)]
    member.guild.fetch_member.side_effect = [member, target, member]
    bot.is_owner.side_effect = lambda user: user.id == member.id
    account = Account(
        id=100, consent=PrivacyNoticeReceipt.grant_current(), identities=(AccountIdentity.discord(target.id),)
    )
    use_id = uuid4()
    bot.services.impersonation.use = AsyncMock(
        side_effect=[AuthorizedImpersonation(account, target.id, use_id), AuthorizationError()]
    )
    channel = Mock(spec=discord.TextChannel)
    channel.permissions_for.return_value = discord.Permissions(view_channel=True)
    request = Mock(spec=sd.Request, user=member, guild=member.guild, channel=channel)
    session_id = uuid4()
    ingress = ActingContext.direct(UserActor(discord_user_id=member.id))
    with ingress.scope():
        effective = await bot.actors.request_actor(request, session_id, "draft:example")
        context = ActingContext.current()
        assert context.effective is effective
        assert context.invocation_id == ingress.invocation_id
        assert impersonation_use.get() == use_id
    assert current_acting_context.get() is None
    assert impersonation_use.get() is None
    assert context.operator.require_user().is_bot_owner
    assert context.effective.account_id == account.id
    assert context.effective.discord_role_ids == {55}
    assert not context.effective.is_bot_owner
    assert not context.effective.discord_guild_admin
    assert context.effective.audit_use_id == use_id
    with pytest.raises(AuthorizationError):
        await bot.actors.request_actor(request, session_id, "draft:example")
