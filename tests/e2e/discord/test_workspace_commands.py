"""Exercise the guild workspaces through their registered application commands."""

from uuid import UUID

import discord
import pytest
import simcord

from squid.bot.diagnostics.screen import ErrorReportScreen
from squid.core.accounts.domain import AccountConsent, PublicCreatorProfile
from squid.core.accounts.models import Account, CreatorAlias
from squid.core.permissions.domain.catalogue import DIAGNOSTICS_ERROR_READ, PERM_GRANT_GUILD, SETTINGS_SERVER_VIEW
from squid.core.settings.domain.guild import GuildSettings
from squid.core.voting.domain.models import VoteConfiguration, VoteKind
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import (
    AccessRevision,
    GuildConfiguration,
    StoredReports,
    WorkspaceAccounts,
    WorkspaceInbox,
    staff_member,
    staff_permissions,
)
from tests.support.services import bot_services


def assert_private_defer_was_completed(env: simcord.Env) -> None:
    (callback,) = (call for call in env.http_requests if call.method == "POST" and call.path.endswith("/callback"))
    assert callback.json is not None
    assert callback.json["type"] == discord.InteractionResponseType.deferred_channel_message.value
    assert callback.json["data"]["flags"] & discord.MessageFlags(ephemeral=True).value
    assert any(call.method == "PATCH" and call.path.endswith("/messages/@original") for call in env.http_requests)


async def test_creator_page_action_on_an_unlinked_member_explains_privately() -> None:
    accounts = WorkspaceAccounts()
    async with command_environment("squid.bot.accounts.cog", services=bot_services(accounts=accounts)) as (_, env):
        channel = env.guild.create_text_channel("public")
        visitor = env.guild.add_member(env.create_user("visitor"))
        creator = env.guild.add_member(env.create_user("builder"), nick="Red Builder")

        result = await visitor.context_menu(channel, "Creator Page", creator)

        assert result.response is not None
        assert result.response.ephemeral
        assert any(
            "Red Builder doesn't have a creator page." in text for text in displayed_text(result.response.message)
        )
        assert str(creator.id) in accounts.lookups


async def test_creator_accepts_a_pasted_mention() -> None:
    accounts = WorkspaceAccounts()
    async with command_environment("squid.bot.accounts.cog", services=bot_services(accounts=accounts)) as (_, env):
        channel = env.guild.create_text_channel("public")
        visitor = env.guild.add_member(env.create_user("visitor"))
        creator = env.guild.add_member(env.create_user("builder"), nick="Red Builder")

        result = await visitor.slash(channel, "creator", name=f"<@!{creator.id}>")

        assert result.response is not None
        assert result.response.ephemeral
        assert any(
            "Red Builder doesn't have a creator page." in text for text in displayed_text(result.response.message)
        )
        assert accounts.lookups == [str(creator.id)]


async def test_creator_finds_a_typed_credited_name_and_posts_it_publicly() -> None:
    accounts = WorkspaceAccounts()
    public_id = UUID(int=5)
    accounts.aliases["glinte"] = CreatorAlias(name="Glinte", account_id=1, public_creator_id=public_id)
    accounts.profiles[public_id] = PublicCreatorProfile(public_id=public_id, hidden=False, display_name="Glinte")
    async with command_environment("squid.bot.accounts.cog", services=bot_services(accounts=accounts)) as (_, env):
        channel = env.guild.create_text_channel("public")
        visitor = env.guild.add_member(env.create_user("visitor"))

        found = await visitor.slash(channel, "creator", name="GLINTE")
        missing = await visitor.slash(channel, "creator", name="Nobody")

        assert found.response is not None
        assert not found.response.ephemeral
        assert any("Glinte" in text for text in displayed_text(found.response.message))
        assert missing.response is not None
        assert missing.response.ephemeral
        assert any("Nobody doesn't have a creator page." in text for text in displayed_text(missing.response.message))


async def test_unknown_screen_address_finishes_the_private_defer_with_navigation_help() -> None:
    async with command_environment("squid.bot.navigation.cog", services=bot_services()) as (_, env):
        channel = env.guild.create_text_channel("builds")
        member = env.guild.add_member(env.create_user("builder"))

        result = await member.slash(channel, "open", address="missing/workspace")

        assert_private_defer_was_completed(env)
        assert result.response is not None
        assert result.response.ephemeral
        assert any(
            "That screen address is invalid" in text and "/builds" in text
            for text in displayed_text(result.response.message)
        )


async def test_notifications_show_a_consented_members_personal_inbox() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(accounts=accounts, notifications=WorkspaceInbox(7), permissions=staff_permissions())
    async with command_environment("squid.bot.accounts.cog", "squid.bot.notifications_cog", services=services) as (
        _,
        env,
    ):
        channel = env.guild.create_text_channel("public")
        member = env.guild.add_member(env.create_user("builder"))
        accounts.discord_accounts[str(member.id)] = Account(id=7, consent=AccountConsent.grant_current())

        result = await member.slash(channel, "notifications")

        assert result.response is not None
        assert result.response.ephemeral
        summary = "\n".join(displayed_text(result.response.message))
        assert "1 unread" in summary
        assert "0 follows" in summary

        changed = await member.click(result.response.message, label="Accounts")

        assert changed.acknowledged
        accounts_tab = displayed_text(result.response.message)
        assert any("Connect Minecraft" in text for text in accounts_tab)
        assert any("Merge Squid accounts" in text for text in accounts_tab)

        await member.click(result.response.message, label="Credits")

        assert any("Claim credit for your builds" in text for text in displayed_text(result.response.message))

        opened = await member.slash(channel, "account")

        assert opened.response is not None
        assert opened.response.ephemeral
        assert any("Creator profile" in text for text in displayed_text(opened.response.message))


async def test_access_opens_with_the_selected_discord_role_in_the_invoking_guild() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(
        accounts=accounts, permissions=staff_permissions(PERM_GRANT_GUILD), permission_admin=AccessRevision()
    )
    async with command_environment("squid.bot.permissions_cog", services=services) as (_, env):
        channel = env.guild.create_text_channel("staff")
        staff = staff_member(env, accounts)
        builders = env.guild.create_role("Redstone Builders")

        result = await staff.slash(channel, "access", role=builders)

        assert result.response is not None
        assert result.response.ephemeral
        assert any(f"Redstone Builders in {env.guild.name}" in text for text in displayed_text(result.response.message))


async def test_settings_renders_the_channel_configured_for_this_guild() -> None:
    accounts, settings = WorkspaceAccounts(), GuildConfiguration()
    services = bot_services(accounts=accounts, permissions=staff_permissions(SETTINGS_SERVER_VIEW), settings=settings)
    async with command_environment("squid.bot.settings.cog", services=services) as (_, env):
        channel = env.guild.create_text_channel("staff")
        builds = env.guild.create_text_channel("builds")
        staff = staff_member(env, accounts)
        settings.snapshots[env.guild.id] = GuildSettings(
            builds_channel_id=builds.id, voting={kind: VoteConfiguration(None, ()) for kind in VoteKind}
        )

        result = await staff.slash(channel, "settings")

        assert result.response is not None
        assert result.response.ephemeral
        summary = "\n".join(displayed_text(result.response.message))
        assert "Server settings" in summary
        assert f"<#{builds.id}>" in summary


@pytest.mark.parametrize("allowed", [True, False])
async def test_errors_checks_access_before_loading_reports(allowed: bool) -> None:
    accounts, reports = WorkspaceAccounts(), StoredReports()
    nodes = (DIAGNOSTICS_ERROR_READ,) if allowed else ()
    services = bot_services(accounts=accounts, permissions=staff_permissions(*nodes), error_reports=reports)
    async with command_environment("squid.bot.diagnostics.cog", services=services) as (bot, env):
        channel = env.guild.create_text_channel("staff")
        staff = staff_member(env, accounts)

        result = await staff.slash(channel, "errors")

        assert result.response is not None
        assert result.response.ephemeral
        detail = "\n".join(displayed_text(result.response.message))
        if allowed:
            assert reports.recent_calls
            assert reports.report.reference in detail
            (session,) = bot.sessions.active()
            (root,) = session.message_roots
            assert isinstance(root.component, ErrorReportScreen)
            opening = root.component.opening
            assert opening is not None
            assert opening.owner is bot.get_cog("Diagnostics")
            await bot.remove_cog("Diagnostics")
            assert root.finished
        else:
            assert not reports.recent_calls
            assert "diagnostics.error.read" in detail
            assert not tuple(bot.sessions.active())
            (captured,) = env.errors
            assert isinstance(captured, discord.app_commands.CommandInvokeError)


async def test_errors_opens_the_referenced_report_with_its_full_text_attachment() -> None:
    accounts, reports = WorkspaceAccounts(), StoredReports()
    services = bot_services(
        accounts=accounts, permissions=staff_permissions(DIAGNOSTICS_ERROR_READ), error_reports=reports
    )
    async with command_environment("squid.bot.diagnostics.cog", services=services) as (_, env):
        channel = env.guild.create_text_channel("staff")
        staff = staff_member(env, accounts)

        result = await staff.slash(channel, "errors", reference=reports.report.reference)

        assert result.response is not None
        assert result.response.ephemeral
        detail = "\n".join(displayed_text(result.response.message))
        assert reports.report.correlation_id in detail
        assert reports.report.traceback in detail
        (attachment,) = result.response.attachments
        assert attachment.filename == f"error-{reports.report.reference}.txt"


async def test_connection_privately_reports_the_last_disconnect_and_resume() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(accounts=accounts, permissions=staff_permissions(DIAGNOSTICS_ERROR_READ))
    async with command_environment("squid.bot.diagnostics.cog", services=services) as (bot, env):
        channel = env.guild.create_text_channel("staff")
        staff = staff_member(env, accounts)
        bot.gateway_history.record("disconnect")
        bot.gateway_history.record("resume")

        result = await staff.slash(channel, "connection")

        assert_private_defer_was_completed(env)
        assert result.response is not None
        assert result.response.ephemeral
        card = "\n".join(displayed_text(result.response.message))
        assert "Discord connection" in card
        assert "1 resumes, 1 disconnects" in card
