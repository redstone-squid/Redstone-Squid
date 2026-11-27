"""Gateway members become stored voter memberships."""

from unittest.mock import AsyncMock, Mock

import discord

from squid.bot.voting.members import VoterMemberSync
from squid.core.actors import ActingContext
from squid.core.voting.domain import GuildMember


def member(user_id: int, guild: discord.Guild, *roles: int, bot: bool = False) -> discord.Member:
    fake = Mock(spec=discord.Member, id=user_id, guild=guild, bot=bot, roles=[discord.Object(role) for role in roles])
    fake.guild_permissions = discord.Permissions.none()
    return fake


def sync() -> tuple[VoterMemberSync, AsyncMock]:
    votes = Mock(record_members=AsyncMock(), forget_member=AsyncMock())
    return VoterMemberSync(Mock(services=Mock(votes=votes), owner_server_id=1)), votes.record_members


async def test_only_the_owner_guilds_members_are_recorded_and_never_bots() -> None:
    first, second = Mock(spec=discord.Guild, id=1), Mock(spec=discord.Guild, id=2)
    members, record = sync()

    with ActingContext.worker("discord.gateway").scope():
        await members.record((member(10, first, 5), member(11, first, bot=True), member(12, second)))

    assert [call.args for call in record.await_args_list] == [(1, [GuildMember(10, frozenset({5}))])]


async def test_reconcile_waits_for_a_complete_member_cache() -> None:
    guild = Mock(spec=discord.Guild, id=1, chunked=False)
    members, record = sync()

    with ActingContext.worker("discord.gateway").scope():
        await members.reconcile(guild)
        assert record.await_count == 0

        guild.chunked = True
        guild.members = [member(10, guild, 5)]
        await members.reconcile(guild)
    record.assert_awaited_once()
    (call,) = record.await_args_list
    assert call.args == (1, [GuildMember(10, frozenset({5}))])
    assert call.kwargs["complete"] is True
