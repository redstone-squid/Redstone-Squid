"""Exercise message and reaction listeners through simulated Discord gateway events."""

from dataclasses import replace
from unittest.mock import ANY

import pytest

from squid.bot.events.spec import extension as events_extension
from squid.bot.starboard.spec import extension as starboard_extension
from squid.bot.voting.spec import extension as vote_extension
from squid.core.accounts.domain import AccountConsent
from squid.core.actors import ActingContext
from squid.core.posts.domain import StarboardEntryKey
from squid.core.starboard.application.services import StarboardVoteResult
from squid.core.voting.domain import CastVoteResult, VoteMessage, VoteSelection, VoteVisibility
from squid_ui.ext.discord.testing import AsyncCallRecorder
from tests.e2e.discord.community_services import Boards, CommunityVotes, EmptyDomainEvents, ObservedMessages, VotePosts
from tests.e2e.discord.environment import allow_idle_reaction_workers, command_environment
from tests.e2e.discord.workspace_services import staff_permissions
from tests.support.accounts import FakeAccountRepository, InMemoryAccountService
from tests.support.services import bot_services
from tests.support.voting import poll_snapshot


async def test_message_edit_and_delete_reach_tracking_and_starboard(monkeypatch: pytest.MonkeyPatch) -> None:
    messages, boards = ObservedMessages(), Boards()
    edit = AsyncCallRecorder(result=True)
    delete = AsyncCallRecorder(result=True)
    refresh = AsyncCallRecorder(result=())
    delete_origin = AsyncCallRecorder(result=())
    monkeypatch.setattr(messages, "record_edit", edit)
    monkeypatch.setattr(messages, "mark_deleted", delete)
    monkeypatch.setattr(boards, "refresh", refresh)
    monkeypatch.setattr(boards, "mark_origin_deleted", delete_origin)
    services = bot_services(messages=messages, starboards=boards)
    async with command_environment("squid.bot.messages_cog", starboard_extension, services=services) as (_, env):
        channel = env.guild.create_text_channel("builds")
        builder = env.guild.add_member(env.create_user("builder"))
        message = await builder.send(channel, "Original dimensions")

        await builder.edit(message, "Corrected dimensions")
        edit.assert_awaited_once_with(message.id, "Corrected dimensions", context=ANY)
        refresh.assert_awaited_once_with(message.id, force=True, context=ANY)

        await builder.delete(message)
        delete.assert_awaited_once_with(message.id, context=ANY)
        delete_origin.assert_awaited_once_with(message.id, context=ANY)
        assert not channel.history()


async def test_public_poll_reaction_casts_and_withdraws_a_ballot(monkeypatch: pytest.MonkeyPatch) -> None:
    accounts, votes, posts = FakeAccountRepository(), CommunityVotes(), VotePosts()
    services = bot_services(
        accounts=InMemoryAccountService(accounts),
        permissions=staff_permissions(),
        votes=votes,
        posts=posts,
        messages=ObservedMessages(),
        domain_events=EmptyDomainEvents(),
    )
    async with command_environment(events_extension, vote_extension, services=services) as (bot, env):
        allow_idle_reaction_workers(bot, env, monkeypatch)
        channel = env.guild.create_text_channel("voting")
        author = env.guild.add_member(env.create_user("author"))
        voter = env.guild.add_member(env.create_user("voter"))
        accounts.seed_account(author.id, account_id=17, consent=AccountConsent.grant_current())
        accounts.seed_account(voter.id, account_id=18, consent=AccountConsent.grant_current())

        await author.slash(channel, "poll", question="Ready?", visibility=VoteVisibility.VISIBLE_LIVE.value)
        (card,) = channel.history()
        (creation,) = votes.poll_creations.records
        assert creation.kwargs["visibility"] is VoteVisibility.VISIBLE_LIVE
        session = poll_snapshot(
            id=42,
            author_account_id=17,
            guild_id=env.guild.id,
            visibility=VoteVisibility.VISIBLE_LIVE,
            menu=creation.kwargs["menu"],
            messages=(VoteMessage(card.id, channel.id, env.guild.id),),
        )

        def cast(session_id: int, option_id: str, *, emoji: str, context: ActingContext) -> CastVoteResult:
            nonlocal session
            assert session_id == session.id
            actor = context.effective.require_user()
            previous = session.selection_for(actor.require_account_id())
            selections = (
                () if previous else (VoteSelection(actor.require_account_id(), actor.guild_id, option_id, emoji, 1),)
            )
            session = replace(session, selections=selections)
            return CastVoteResult(session)

        cast_vote = AsyncCallRecorder(callback=cast)
        monkeypatch.setattr(votes, "cast_vote", cast_vote)
        monkeypatch.setattr(
            votes,
            "get_session_for_message",
            AsyncCallRecorder(callback=lambda message_id, *, context: session if message_id == card.id else None),
        )
        monkeypatch.setattr(
            votes,
            "get_session",
            AsyncCallRecorder(callback=lambda session_id, *, context: session if session_id == session.id else None),
        )

        await voter.react(card, "👍")
        selection = session.selection_for(18)
        assert selection is not None
        assert (selection.guild_id, selection.emoji) == (env.guild.id, "👍")
        assert cast_vote.await_count == 1

        await voter.unreact(card, "👍")
        assert session.selection_for(18) is None
        assert cast_vote.await_count == 2
        assert cast_vote.records[1] == cast_vote.records[0]


async def test_starboard_reaction_records_and_withdraws_a_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    boards, posts = Boards(), VotePosts()
    record = AsyncCallRecorder()
    withdraw = AsyncCallRecorder()
    pending = AsyncCallRecorder()
    monkeypatch.setattr(boards, "record_vote", record)
    monkeypatch.setattr(boards, "withdraw_vote", withdraw)
    monkeypatch.setattr(
        boards, "is_relevant_emoji", AsyncCallRecorder(callback=lambda guild_id, emoji, *, context: emoji == "⭐")
    )
    monkeypatch.setattr(posts, "pending_generation", pending)
    async with command_environment(starboard_extension, services=bot_services(starboards=boards, posts=posts)) as (
        bot,
        env,
    ):
        allow_idle_reaction_workers(bot, env, monkeypatch)
        channel = env.guild.create_text_channel("builds")
        builder = env.guild.add_member(env.create_user("builder"))
        voter = env.guild.add_member(env.create_user("voter"))
        message = await builder.send(channel, "A new design")
        record.result = StarboardVoteResult(((4, message.id),))
        withdraw.result = ((4, message.id),)

        await voter.react(message, "👍")
        record.assert_not_awaited()
        await voter.react(message, "⭐")
        (vote,) = record.records
        origin, emoji = vote.args
        actor = vote.kwargs["context"].effective
        assert (origin.id, origin.message.guild_id, origin.message.channel_id, origin.message.author_id) == (
            message.id,
            env.guild.id,
            channel.id,
            builder.id,
        )
        assert (actor.discord_user_id, actor.guild_id, emoji) == (voter.id, env.guild.id, "⭐")
        await env.advance_time(2)
        key = str(StarboardEntryKey(4, message.id))
        pending.assert_awaited_once_with("starboard_entry", key, context=ANY)

        await voter.unreact(message, "⭐")
        withdraw.assert_awaited_once_with(message.id, "⭐", context=ANY)
        withdrawn_by = withdraw.records[0].kwargs["context"].effective
        assert (withdrawn_by.discord_user_id, withdrawn_by.guild_id) == (voter.id, env.guild.id)
        await env.advance_time(2)
        assert pending.await_count == 2
        assert pending.records[1] == pending.records[0]
