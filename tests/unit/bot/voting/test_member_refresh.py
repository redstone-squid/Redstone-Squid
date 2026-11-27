"""Voter resolution keeps a known absence distinct from a failed lookup."""

from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from squid.bot.app import RedstoneSquid
from squid.bot.voting.cog import VoteCog
from squid.core.accounts.application import AccountService
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain import PermissionCheck
from squid.core.voting.domain import VoteKind
from tests.support.services import bot_services

ROSTER = {
    1: Account(id=1, identities=(AccountIdentity.discord(70),)),
    2: Account(id=2, identities=(AccountIdentity.discord(80),)),
    3: Account(id=3, identities=(AccountIdentity.discord(90),)),
    4: Account(id=4, identities=()),
}


@pytest.mark.parametrize(
    ("account_id", "outcome"),
    [(1, "member"), (2, "absent"), (3, "unknown"), (4, "unlinked")],
)
async def test_resolve_distinguishes_known_absence_from_lookup_failure(account_id: int, outcome: str) -> None:
    accounts = AccountService(AsyncMock(), lambda: 123456, uow_factory=Mock())
    permissions = PermissionService(uow_factory=Mock())
    bot = RedstoneSquid(bot_services(accounts=accounts, permissions=permissions))
    guild = Mock(spec=discord.Guild, id=10, owner_id=999)
    member = Mock(spec=discord.Member, id=70, guild=guild, roles=(discord.Object(9),))
    member.guild_permissions = discord.Permissions.none()
    cog = VoteCog.__new__(VoteCog)
    cog.bot = bot
    with (
        patch.object(accounts, "get_account_by_id", new_callable=AsyncMock, side_effect=ROSTER.get),
        patch.object(permissions, "check", new_callable=AsyncMock, return_value=PermissionCheck(())),
        patch.object(bot, "get_guild", return_value=guild),
        patch.object(bot, "is_owner", new_callable=AsyncMock, return_value=False),
        patch.object(bot.members, "resolve", new_callable=AsyncMock, return_value={70: member, 80: None}),
    ):
        resolved = await cog.resolve(account_id, 10, VoteKind.BUILD)
    match outcome:
        case "member":
            assert resolved is not None
            assert resolved.discord_role_ids == frozenset({9})
        case "absent":
            assert resolved is not None
            assert resolved.discord_role_ids == frozenset()
        case "unknown":
            assert resolved is None
        case "unlinked":
            assert resolved is not None
            assert resolved.discord_user_id is None
    await bot.close()
