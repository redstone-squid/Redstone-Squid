"""Exercise member, moderator and bot-owner commands through Discord."""

from collections.abc import Awaitable, Callable
from functools import partial

import discord
import pytest
import simcord
from discord.ext import commands

from squid.bot.recount.spec import extension as recount_extension
from squid.bot.starboard.spec import extension as starboard_extension
from squid.bot.voting.spec import extension as vote_extension
from squid.core.accounts.domain import AccountConsent
from squid.core.accounts.models import Account
from squid.core.permissions.domain.catalogue import (
    MESSAGE_ARCHIVE_CREATE,
    REDSTONER_PANEL_MANAGE,
    SETTINGS_RUNTIME_VIEW,
    STARBOARD_BOARD_VIEW,
    VOTE_POLL_RECOUNT,
)
from squid.core.starboard.models import StarboardConfig
from squid.core.voting.domain import VoteStatus
from tests.e2e.discord.community_services import (
    Boards,
    CommunitySettings,
    CommunityVotes,
    ObservedMessages,
    OwnerReports,
    PublishedTags,
    VotePosts,
)
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import WorkspaceAccounts, staff_permissions
from tests.support.services import bot_services
from tests.support.voting import build_snapshot

ADMIN = "squid.bot.admin_cog"


def community_moderator(env: simcord.Env, accounts: WorkspaceAccounts) -> simcord.MemberActor:
    role = env.guild.create_role(
        "Moderators", permissions=discord.Permissions(manage_guild=True, manage_roles=True, manage_messages=True)
    )
    moderator = env.guild.add_member(env.create_user("moderator"), roles=[role])
    accounts.discord_accounts[str(moderator.id)] = Account(id=17, consent=AccountConsent.grant_current())
    return moderator


async def test_archive_then_paste_moves_the_selected_message_with_its_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(accounts=accounts, permissions=staff_permissions(MESSAGE_ARCHIVE_CREATE))
    async with command_environment(ADMIN, services=services) as (bot, env):
        # Adding the archive's reaction starts the router's persistent workers. An idle
        # shard is waiting for another gateway event while its handlers remain observable.
        async def read_next[T](get: Callable[[], Awaitable[T]]) -> T:
            return await env.external_wait(get(), reason="next Discord reaction")

        for queue in bot.reactions._queues:
            monkeypatch.setattr(queue, "get", partial(read_next, queue.get))
        source = env.guild.create_text_channel("source")
        destination = env.guild.create_text_channel("archive")
        moderator = community_moderator(env, accounts)
        builder = env.guild.add_member(env.create_user("builder"))
        message = await builder.send(source, "The original design", attachments=[("notes.txt", b"repeater notes")])

        selected = await moderator.context_menu(source, "Archive Message", message)
        assert selected.response is not None
        assert selected.response.ephemeral
        assert any("Run /paste" in text for text in displayed_text(selected.response.message))
        assert message.id in [item.id for item in source.history()]

        pasted = await moderator.slash(destination, "paste")
        assert pasted.response is not None
        copy = pasted.response.message
        assert not copy.flags.ephemeral
        assert "The original design" in copy.content
        assert builder.mention in copy.content
        assert [reaction.emoji for reaction in copy.reactions] == ["❌"]
        (attachment,) = copy.attachments
        assert attachment.filename == "notes.txt"
        assert await attachment.read() == b"repeater notes"
        assert message.id not in [item.id for item in source.history()]

        again = await moderator.slash(destination, "paste")
        assert again.response is not None
        assert any("Select a message" in text for text in displayed_text(again.response.message))


@pytest.mark.parametrize("entry", ["slash", "prefix"])
async def test_runtime_settings_displays_the_current_value_and_revision(entry: str) -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(
        accounts=accounts, permissions=staff_permissions(SETTINGS_RUNTIME_VIEW), settings=CommunitySettings()
    )
    async with command_environment(ADMIN, services=services) as (_, env):
        channel = env.guild.create_text_channel("operators")
        moderator = community_moderator(env, accounts)
        if entry == "slash":
            reply = await moderator.slash(channel, "runtime-settings", setting="welcome_forward_chance")
            assert reply.response is not None
            assert reply.response.ephemeral
            message = reply.response.message
        else:
            await moderator.send(channel, "!runtime-settings welcome_forward_chance")
            message = channel.last_message
            assert message is not None
        text = "\n".join(displayed_text(message))
        assert r"welcome\_forward\_chance: 0.1 (revision 1)" in text
        assert '"type": "number"' in text


@pytest.mark.parametrize(
    ("name", "label", "domain"),
    [("gdb", "Open spreadsheet", "docs.google.com"), ("db", "Open database", "supabase.com")],
)
async def test_owner_shortcuts_deliver_the_destination_link(name: str, label: str, domain: str) -> None:
    async with command_environment(ADMIN) as (bot, env):
        channel = env.guild.create_text_channel("operators")
        owner = env.guild.add_member(env.create_user("owner"))
        bot.owner_id = owner.id
        await owner.send(channel, f"!{name}")
        message = channel.last_message
        assert message is not None
        view = discord.ui.LayoutView.from_message(message)
        (link,) = [item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == label]
        assert link.url is not None
        assert link.url.startswith(f"https://{domain}/")


async def test_owner_sync_copies_commands_to_current_guild() -> None:
    async with command_environment(ADMIN) as (bot, env):
        channel = env.guild.create_text_channel("operators")
        owner = env.guild.add_member(env.create_user("owner"))
        bot.owner_id = owner.id
        await owner.send(channel, "!s *")
        message = channel.last_message
        assert message is not None
        assert any("to the current guild" in text for text in displayed_text(message))
        assert any(
            call.method == "PUT" and call.path.endswith(f"/guilds/{env.guild.id}/commands")
            for call in env.http_requests
        )


async def test_tags_shows_the_published_library_to_a_visitor() -> None:
    services = bot_services(accounts=WorkspaceAccounts(), permissions=staff_permissions(), tags=PublishedTags())
    async with command_environment(ADMIN, services=services) as (_, env):
        channel = env.guild.create_text_channel("catalogue")
        visitor = env.guild.add_member(env.create_user("visitor"))
        result = await visitor.slash(channel, "tags")
        assert result.response is not None
        assert any("Hip door" in text for text in displayed_text(result.response.message))


async def test_redstoner_deploys_a_public_panel_from_the_home_server() -> None:
    accounts = WorkspaceAccounts()
    settings = CommunitySettings()
    services = bot_services(accounts=accounts, settings=settings, permissions=staff_permissions(REDSTONER_PANEL_MANAGE))
    async with command_environment("squid.bot.give_redstoner_cog", services=services) as (bot, env):
        channel = env.guild.create_text_channel("roles")
        moderator = community_moderator(env, accounts)
        bot.owner_server_id = env.guild.id
        result = await moderator.slash(channel, "redstoner")
        assert result.response is not None
        assert result.response.ephemeral
        await moderator.click(result.response.message, label="Deploy role controls")
        panel = channel.last_message
        assert panel is not None
        assert not panel.flags.ephemeral
        assert "Redstoner role controls" in displayed_text(panel)
        assert any(
            isinstance(item, discord.ui.Button) and item.label == "I'm not a redstoner"
            for item in discord.ui.LayoutView.from_message(panel).walk_children()
        )


async def test_starboard_shows_the_selected_guilds_board() -> None:
    accounts = WorkspaceAccounts()
    boards = Boards()
    services = bot_services(accounts=accounts, starboards=boards, permissions=staff_permissions(STARBOARD_BOARD_VIEW))
    async with command_environment(starboard_extension, services=services) as (_, env):
        channel = env.guild.create_text_channel("staff")
        moderator = community_moderator(env, accounts)
        boards.boards[env.guild.id] = (
            StarboardConfig(id=8, guild_id=env.guild.id, channel_id=channel.id, name="Golden builds"),
        )
        reply = await moderator.slash(channel, "starboard")
        assert reply.response is not None
        assert reply.response.ephemeral
        assert any("Golden builds" in text for text in displayed_text(reply.response.message))


async def test_review_lists_only_the_invoking_guilds_pending_polls() -> None:
    accounts = WorkspaceAccounts()
    votes = CommunityVotes()
    services = bot_services(accounts=accounts, permissions=staff_permissions(VOTE_POLL_RECOUNT), votes=votes)
    async with command_environment(vote_extension, services=services) as (_, env):
        channel = env.guild.create_text_channel("staff")
        moderator = community_moderator(env, accounts)
        votes.reviews[env.guild.id] = (24, 31)
        reply = await moderator.slash(channel, "poll-review")
        assert reply.response is not None
        assert reply.response.ephemeral
        assert "Polls awaiting review: 24, 31" in displayed_text(reply.response.message)


async def test_poll_preview_shows_question_and_options_without_posting_a_card() -> None:
    accounts = WorkspaceAccounts()
    services = bot_services(accounts=accounts, permissions=staff_permissions(), votes=CommunityVotes())
    async with command_environment(vote_extension, services=services) as (_, env):
        channel = env.guild.create_text_channel("voting")
        moderator = community_moderator(env, accounts)
        result = await moderator.slash(channel, "poll", question="Which door?", choices="Hip, Vault", preview=True)
        assert result.response is not None
        assert result.response.ephemeral
        text = "\n".join(displayed_text(result.response.message))
        for part in ("Which door?", "Hip", "Vault"):
            assert part in text
        assert channel.history() == []


async def test_message_deletion_vote_posts_a_public_card_for_the_selected_message() -> None:
    accounts = WorkspaceAccounts()
    votes = CommunityVotes()
    posts = VotePosts()
    messages = ObservedMessages()
    services = bot_services(
        accounts=accounts, votes=votes, posts=posts, messages=messages, settings=CommunitySettings()
    )
    async with command_environment(vote_extension, services=services) as (_, env):
        channel = env.guild.create_text_channel("builds")
        voter = community_moderator(env, accounts)
        builder = env.guild.add_member(env.create_user("builder"))
        target = await builder.send(channel, "obsoleted build")
        result = await voter.context_menu(channel, "Vote to Delete", target)
        assert result.response is not None
        assert result.response.ephemeral
        assert "Deletion vote opened." in displayed_text(result.response.message)
        assert votes.deletion_targets == [(17, env.guild.id, channel.id, target.id)]
        card = channel.last_message
        assert card is not None
        assert posts.cards == {"42": (channel.id, card.id)}
        assert [item.id for item in messages.observed] == [card.id]
        assert not card.flags.ephemeral


@pytest.mark.parametrize("entry", ["link", "menu"])
async def test_recount_recognizes_the_selected_closed_vote(entry: str) -> None:
    votes = CommunityVotes()
    services = bot_services(votes=votes)
    async with command_environment(vote_extension, starboard_extension, recount_extension, services=services) as (
        _,
        env,
    ):
        channel = env.guild.create_text_channel("voting")
        voter = env.guild.add_member(env.create_user("voter"))
        target = await voter.send(channel, "finished poll")
        votes.cards[target.id] = build_snapshot(status=VoteStatus.CLOSED)
        if entry == "link":
            result = await voter.slash(channel, "recount", message=target.jump_url)
        else:
            result = await voter.context_menu(channel, "Recount reactions", target)
        assert result.response is not None
        assert result.response.ephemeral
        assert "That vote is already closed." in displayed_text(result.response.message)


async def test_owner_error_command_presents_the_reference_saved_with_the_failure() -> None:
    reports = OwnerReports()
    async with command_environment(ADMIN, "squid.bot.log_cog", services=bot_services(error_reports=reports)) as (
        bot,
        env,
    ):
        channel = env.guild.create_text_channel("operators")
        owner = env.guild.add_member(env.create_user("owner"))
        bot.owner_id = owner.id

        await owner.send(channel, "!raise-error")

        ((reference, error),) = reports.failures.items()
        (captured,) = env.errors
        assert isinstance(captured, commands.CommandInvokeError)
        assert captured.original is error
        assert str(error) == "This is a test error."
        reply = channel.last_message
        assert reply is not None
        assert any(reference in text for text in displayed_text(reply))
