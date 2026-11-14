"""Actor resolution preserves owner authority while refreshing mutable identity."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext.commands import Context

import squid_ui_discord as sd
from squid.accounts.models import Account
from squid.bot.actors import AccountIdCache, DiscordActorResolver
from squid.bot.app import RedstoneSquid
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
