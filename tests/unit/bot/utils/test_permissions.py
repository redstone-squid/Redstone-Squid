"""Discord-facing permission hierarchy tests."""

from types import SimpleNamespace
from typing import cast

import discord
import pytest
from discord.ext.commands import Context

from squid.bot.app import RedstoneSquid
from squid.bot.utils.permissions import (
    GlobalAdministratorRequired,
    TrustedOrGlobalAdministratorRequired,
    check_is_global_admin,
    check_is_trusted_or_global_admin,
    is_global_admin,
    is_server_admin,
    is_trusted_or_global_admin,
)


class FakeAuthorizationService:
    def __init__(self, administrator_ids: set[int] | None = None) -> None:
        self.administrator_ids = administrator_ids or set()

    async def is_global_administrator(self, account_id: int) -> bool:
        return account_id in self.administrator_ids


class FakeAccountService:
    async def get_account(self, discord_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=discord_id)


class FakeSettingsService:
    def __init__(self, trusted_role_ids: list[int] | None = None) -> None:
        self.trusted_role_ids = trusted_role_ids or []

    async def get(self, server_id: int, setting: str) -> list[int]:
        assert setting == "Trusted"
        return self.trusted_role_ids


def _member(user_id: int, guild: SimpleNamespace, **permissions: bool) -> SimpleNamespace:
    guild_permissions = discord.Permissions.none()
    guild_permissions.update(**permissions)
    return SimpleNamespace(
        id=user_id,
        guild=guild,
        guild_permissions=guild_permissions,
        roles=[SimpleNamespace(id=30)],
    )


def _bot(
    *, owner_id: int = 1, global_admin_ids: set[int] | None = None, member_id: int | None = None
) -> tuple[RedstoneSquid, SimpleNamespace]:
    guild = SimpleNamespace(id=100, owner_id=2)
    member = _member(member_id, guild) if member_id is not None else None
    guild.get_member = lambda user_id: member if member is not None and member.id == user_id else None
    services = SimpleNamespace(
        accounts=FakeAccountService(),
        authorization=FakeAuthorizationService(global_admin_ids),
        settings=FakeSettingsService([30]),
    )

    async def is_owner(user: object) -> bool:
        return getattr(user, "id", None) == owner_id

    bot = SimpleNamespace(
        owner_id=owner_id,
        owner_ids=None,
        services=services,
        get_guild=lambda server_id: guild if server_id == guild.id else None,
        is_owner=is_owner,
    )
    return cast(RedstoneSquid, bot), guild


async def test_owner_and_persisted_grant_are_global_administrators() -> None:
    bot, _ = _bot(global_admin_ids={3})

    assert await is_global_admin(bot, 1)
    assert await is_global_admin(bot, 3)
    assert not await is_global_admin(bot, 4)


async def test_server_admin_inherits_global_and_discord_permissions() -> None:
    global_bot, _ = _bot(global_admin_ids={3})
    owner_bot, _ = _bot(member_id=2)
    manager_bot, manager_guild = _bot(member_id=4)
    manager = _member(4, manager_guild, manage_guild=True)
    manager_guild.get_member = lambda user_id: manager if user_id == manager.id else None
    administrator_bot, administrator_guild = _bot(member_id=5)
    administrator = _member(5, administrator_guild, administrator=True)
    administrator_guild.get_member = lambda user_id: administrator if user_id == administrator.id else None
    ordinary_bot, _ = _bot(member_id=6)

    assert await is_server_admin(global_bot, None, 3)
    assert await is_server_admin(owner_bot, 100, 2)
    assert await is_server_admin(manager_bot, 100, 4)
    assert await is_server_admin(administrator_bot, 100, 5)
    assert not await is_server_admin(ordinary_bot, 100, 6)
    assert not await is_server_admin(ordinary_bot, None, 6)


async def test_trusted_role_is_orthogonal_to_server_administration() -> None:
    trusted_bot, _ = _bot(member_id=7)
    global_bot, _ = _bot(global_admin_ids={3})
    ordinary_bot, ordinary_guild = _bot(member_id=8)
    ordinary = _member(8, ordinary_guild)
    ordinary.roles = []
    ordinary_guild.get_member = lambda user_id: ordinary if user_id == ordinary.id else None

    assert await is_trusted_or_global_admin(trusted_bot, 100, 7)
    assert await is_trusted_or_global_admin(global_bot, None, 3)
    assert not await is_trusted_or_global_admin(ordinary_bot, 100, 8)


async def test_command_checks_report_the_permission_tier_that_was_denied() -> None:
    bot, guild = _bot(member_id=8)
    member = _member(8, guild)
    member.roles = []
    guild.get_member = lambda user_id: member if user_id == member.id else None
    context = cast(Context[RedstoneSquid], SimpleNamespace(bot=bot, author=member, guild=guild))

    with pytest.raises(GlobalAdministratorRequired):
        await check_is_global_admin().predicate(context)
    with pytest.raises(TrustedOrGlobalAdministratorRequired):
        await check_is_trusted_or_global_admin().predicate(context)
