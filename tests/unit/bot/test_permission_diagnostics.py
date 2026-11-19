"""Effective channel overwrites and role hierarchy explain setup failures."""

from typing import cast
from unittest.mock import MagicMock, Mock

import discord
import pytest

from squid.bot.diagnostics.permissions import GuildPermissionDiagnostics
from squid.core.settings.domain.guild import GuildSettings
from squid.core.voting.domain import VoteKind
from squid.core.voting.domain.models import VoteConfiguration


def test_vote_channel_reports_effective_missing_permissions() -> None:
    guild = Mock(spec=discord.Guild)
    channel = Mock(spec=discord.TextChannel)
    permissions = discord.Permissions.all()
    permissions.add_reactions = False
    permissions.send_messages = False
    channel.permissions_for.return_value = permissions
    guild.get_channel_or_thread.return_value = channel
    diagnostic = GuildPermissionDiagnostics(
        guild,
        GuildSettings(voting_channel_id=20, voting={kind: VoteConfiguration(None, ()) for kind in VoteKind}),
        (),
    )
    finding = next(item for item in diagnostic.findings if item.key == "Vote")
    assert "Send Messages" in finding.detail.resolve()
    assert "Add Reactions" in finding.detail.resolve()
    assert "View Channel" not in finding.detail.resolve()
    channel.permissions_for.assert_called_once_with(guild.me)


@pytest.mark.parametrize("archived", [False, True])
def test_threads_use_thread_permissions_and_report_closed_threads(archived: bool) -> None:
    guild = Mock(spec=discord.Guild)
    channel = Mock(spec=discord.Thread, archived=archived, locked=False)
    permissions = discord.Permissions.all()
    permissions.send_messages = False
    channel.permissions_for.return_value = permissions
    guild.get_channel_or_thread.return_value = channel
    diagnostic = GuildPermissionDiagnostics(
        guild,
        GuildSettings(builds_channel_id=20, voting={kind: VoteConfiguration(None, ()) for kind in VoteKind}),
        (),
    )
    finding = next(item for item in diagnostic.findings if item.key == "Builds")
    assert ("archived or locked" if archived else "permissions are present") in finding.detail.resolve()


@pytest.mark.parametrize(
    ("manage_roles", "managed", "higher", "expected"),
    [
        (False, False, False, "Missing Manage Roles"),
        (True, True, False, "Discord manages this role"),
        (True, False, True, "highest role above"),
        (True, False, False, "can assign"),
    ],
)
def test_role_assignment_checks_permission_and_hierarchy(
    manage_roles: bool, managed: bool, higher: bool, expected: str
) -> None:
    guild = Mock(spec=discord.Guild)
    guild.me.guild_permissions = discord.Permissions(manage_roles=manage_roles)
    role = MagicMock(spec=discord.Role, managed=managed)
    role.is_default.return_value = False
    role.__ge__.return_value = higher
    guild.get_role.return_value = role
    diagnostic = GuildPermissionDiagnostics(
        cast(discord.Guild, guild),
        GuildSettings(voting={kind: VoteConfiguration(None, ()) for kind in VoteKind}),
        (),
        redstoner_role_id=55,
    )
    assert expected in diagnostic.findings[-1].detail.resolve()
