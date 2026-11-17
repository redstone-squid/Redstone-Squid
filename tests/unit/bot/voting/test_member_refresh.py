"""Bulk voter resolution preserves unknown membership without repeated account reads."""

from unittest.mock import AsyncMock, Mock, patch

import discord

from squid.accounts.application import AccountService
from squid.accounts.models import Account, AccountIdentity
from squid.bot.app import RedstoneSquid
from squid.bot.voting.cog import VoteCog
from squid.permissions.application import PermissionService
from squid.permissions.domain import PermissionCheck
from squid.voting.domain import VoteKind
from tests.support.services import bot_services


async def test_bulk_refresh_distinguishes_known_absence_from_lookup_failure() -> None:
    accounts = AccountService(AsyncMock(), lambda: 123456, uow_factory=Mock())
    roster = {
        1: Account(id=1, identities=(AccountIdentity.discord(70),)),
        2: Account(id=2, identities=(AccountIdentity.discord(80),)),
        3: Account(id=3, identities=(AccountIdentity.discord(90),)),
        4: Account(id=4, identities=()),
    }
    permissions = PermissionService(uow_factory=Mock())
    bot = RedstoneSquid(bot_services(accounts=accounts, permissions=permissions))
    guild = Mock(spec=discord.Guild, id=10, owner_id=999)
    member = Mock(spec=discord.Member, id=70, guild=guild, roles=(discord.Object(9),))
    member.guild_permissions = discord.Permissions.none()
    cog = VoteCog.__new__(VoteCog)
    cog.bot = bot
    with (
        patch.object(accounts, "get_accounts", new_callable=AsyncMock, return_value=roster) as get_accounts,
        patch.object(accounts, "get_account_by_identity", new_callable=AsyncMock) as identity,
        patch.object(permissions, "check", new_callable=AsyncMock, return_value=PermissionCheck(())),
        patch.object(bot, "get_guild", return_value=guild),
        patch.object(bot, "is_owner", new_callable=AsyncMock, return_value=False),
        patch.object(bot.members, "resolve", new_callable=AsyncMock, return_value={70: member, 80: None}) as lookup,
    ):
        resolved = await cog.resolve_many((1, 2, 3, 4), 10, VoteKind.BUILD)
    get_accounts.assert_awaited_once_with((1, 2, 3, 4))
    identity.assert_not_called()
    lookup.assert_awaited_once_with(guild, (70, 80, 90))
    assert resolved[1] is not None
    assert resolved[1].discord_role_ids == frozenset({9})
    assert resolved[2] is not None
    assert resolved[2].discord_role_ids == frozenset()
    assert resolved[3] is None
    assert resolved[4] is not None
    assert resolved[4].discord_user_id is None
    await bot.close()
