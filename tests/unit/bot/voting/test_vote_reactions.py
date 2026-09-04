"""Discord reaction adaptation for vote sessions, independent of starboard."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from pytest_mock import MockerFixture

from squid.bot.voting.vote import VoteCog
from squid.voting.domain import (
    CastVoteResult,
    VoteActor,
    VoteChoice,
    VoteMessage,
    VoteOption,
    VoteSelection,
    VoteVisibility,
)
from tests.support.discord import make_reaction_payload
from tests.support.voting import GENERIC_OPTIONS, poll_snapshot


@dataclass
class _Reaction:
    emoji: str
    members: tuple[Any, ...]
    me: bool = True

    async def users(self, *, limit: None) -> Any:
        del limit
        for member in self.members:
            yield member


def _cog(mocker: MockerFixture, session: Any) -> tuple[Any, Any, Any]:
    votes = mocker.Mock()
    votes.get_session = mocker.AsyncMock(return_value=session)
    votes.cast_vote = mocker.AsyncMock(return_value=CastVoteResult(session))
    bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        services=SimpleNamespace(votes=votes, accounts=mocker.Mock()),
        refresh_posts=mocker.AsyncMock(),
        account_ids=SimpleNamespace(resolve=mocker.AsyncMock(return_value=7)),
        get_or_fetch_message=mocker.AsyncMock(),
    )
    cog = cast(Any, object.__new__(VoteCog))
    cog.bot = bot
    cog.vote_service = votes
    cog._background_tasks = set()
    return cog, bot, votes


def _event(
    mocker: MockerFixture,
    *,
    event_type: str = "REACTION_ADD",
    message_id: int = 100,
    guild_id: int = 10,
    emoji: str = "1️⃣",
) -> Any:
    payload = make_reaction_payload(
        message_id=message_id,
        channel_id=200,
        guild_id=guild_id,
        user_id=70,
        emoji=emoji,
        event_type=cast(Any, event_type),
    )
    return SimpleNamespace(
        payload=payload,
        emoji=str(payload.emoji),
        message=mocker.AsyncMock(return_value=mocker.Mock()),
        resolve_member=mocker.AsyncMock(return_value=mocker.Mock(bot=False)),
    )


async def test_vote_reconciliation_is_a_supervised_periodic_job(mocker: MockerFixture) -> None:
    cog, bot, _votes = _cog(mocker, poll_snapshot())
    handle = mocker.Mock()
    handle.finished.is_set.return_value = False
    bot.background_tasks = SimpleNamespace(start_periodic=mocker.Mock(return_value=handle))

    await cog.ui_load()

    bot.background_tasks.start_periodic.assert_called_once_with(
        cog.reconcile_open_reactions,
        name="vote-reaction-reconciliation",
        interval=60,
    )
    assert cog._background_tasks == {handle}


async def test_vote_adapter_casts_an_added_reaction(mocker: MockerFixture) -> None:
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10),),
    )
    cog, bot, votes = _cog(mocker, session)
    event = _event(mocker)
    actor = VoteActor(7, 70, guild_id=10)
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))
    resolve = mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))

    await cog.on_reaction_add(event)

    resolve.assert_awaited_once_with(bot, event.resolve_member.return_value, account_id=7)
    votes.cast_vote.assert_awaited_once_with(100, actor, "1️⃣")
    bot.refresh_posts.assert_awaited_once_with("vote_session", str(session.id))


async def test_vote_adapter_keeps_anonymous_reaction_until_ballot_commits(mocker: MockerFixture) -> None:
    session = poll_snapshot(messages=(VoteMessage(100, 200, 10),))
    cog, bot, votes = _cog(mocker, session)
    event = _event(mocker)
    actor = VoteActor(7, 70, guild_id=10)
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))
    mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))
    remove = mocker.patch.object(cog, "_remove_reaction", new=mocker.AsyncMock())
    handle = mocker.Mock()
    handle.finished.is_set.return_value = False
    bot.background_tasks = SimpleNamespace(start=mocker.Mock(return_value=handle))

    votes.cast_vote.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await cog.on_reaction_add(event)

    remove.assert_not_called()
    bot.background_tasks.start.assert_not_called()


async def test_vote_adapter_toggles_a_removed_public_ballot(mocker: MockerFixture) -> None:
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10),),
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    event = _event(mocker, event_type="REACTION_REMOVE")
    actor = VoteActor(7, 70, guild_id=10)
    mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))

    await cog.on_reaction_remove(event)

    bot.account_ids.resolve.assert_awaited_once_with(bot.services.accounts, 70)
    votes.cast_vote.assert_awaited_once_with(100, actor, "1️⃣")
    bot.refresh_posts.assert_awaited_once_with("vote_session", str(session.id))


async def test_recovery_reconciles_public_reactions_to_persisted_selection(mocker: MockerFixture) -> None:
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10),),
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    member = mocker.Mock(spec=discord.Member, id=70, bot=False)
    message = mocker.Mock(
        reactions=[
            _Reaction("1️⃣", ()),
            _Reaction("2️⃣", (member,)),
        ]
    )
    message.add_reaction = mocker.AsyncMock()
    message.remove_reaction = mocker.AsyncMock()
    bot.get_or_fetch_message.return_value = message
    mocker.patch.object(cog, "_reaction_member", new=mocker.AsyncMock(return_value=member))
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))
    actor = VoteActor(7, 70, guild_id=10)
    mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))

    await cog.reconcile_message_reactions(100)

    votes.cast_vote.assert_awaited_once_with(100, actor, "2️⃣")
    bot.refresh_posts.assert_awaited_once_with("vote_session", str(session.id))


async def test_recovery_retries_anonymous_reaction_without_replaying_committed_ballot(
    mocker: MockerFixture,
) -> None:
    session = poll_snapshot(
        messages=(VoteMessage(100, 200, 10),),
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    member = mocker.Mock(spec=discord.Member, id=70, bot=False)
    message = mocker.Mock(reactions=[_Reaction("1️⃣", (member,)), _Reaction("2️⃣", ())])
    message.add_reaction = mocker.AsyncMock()
    message.remove_reaction = mocker.AsyncMock()
    bot.get_or_fetch_message.return_value = message
    mocker.patch.object(cog, "_reaction_member", new=mocker.AsyncMock(return_value=member))
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))

    await cog.reconcile_message_reactions(100)

    votes.cast_vote.assert_not_awaited()
    message.remove_reaction.assert_awaited_once_with("1️⃣", member)


async def test_recovery_does_not_remove_public_ballot_from_an_incomplete_discord_read(
    mocker: MockerFixture,
) -> None:
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10),),
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    bot.get_or_fetch_message.return_value = None

    await cog.reconcile_message_reactions(100)

    votes.cast_vote.assert_not_awaited()
    bot.refresh_posts.assert_not_awaited()


async def test_recovery_compares_multi_guild_aliases_by_stable_option_id(mocker: MockerFixture) -> None:
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=10, label="One"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=10, label="Two"),
        VoteOption("🔴", VoteChoice.GENERIC, identifier="one", guild_id=11, label="One"),
        VoteOption("🔵", VoteChoice.GENERIC, identifier="two", guild_id=11, label="Two"),
    )
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10), VoteMessage(101, 201, 11)),
        options=options,
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    member = mocker.Mock(spec=discord.Member, id=70, bot=False)
    first = mocker.Mock(reactions=[_Reaction("1️⃣", ()), _Reaction("2️⃣", ())])
    second = mocker.Mock(reactions=[_Reaction("🔴", (member,)), _Reaction("🔵", ())])
    first.add_reaction = mocker.AsyncMock()
    second.add_reaction = mocker.AsyncMock()
    bot.get_or_fetch_message.side_effect = [first, second]
    mocker.patch.object(cog, "_reaction_member", new=mocker.AsyncMock(return_value=member))
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))

    await cog.reconcile_message_reactions(100)

    votes.cast_vote.assert_not_awaited()
    second.remove_reaction.assert_not_called()


async def test_event_recovery_is_idempotent_across_multi_guild_aliases(mocker: MockerFixture) -> None:
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=10, label="One"),
        VoteOption("🔴", VoteChoice.GENERIC, identifier="one", guild_id=11, label="One"),
    )
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10), VoteMessage(101, 201, 11)),
        options=options,
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    event = _event(mocker, message_id=101, guild_id=11, emoji="🔴")
    actor = VoteActor(7, 70, guild_id=11)
    mocker.patch.object(cog, "_consented_account_id", new=mocker.AsyncMock(return_value=7))
    mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))

    await cog.recover_reaction_add(event)

    votes.cast_vote.assert_not_awaited()
    bot.refresh_posts.assert_not_awaited()


async def test_alias_remove_recovery_unsets_the_stable_option(mocker: MockerFixture) -> None:
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=10, label="One"),
        VoteOption("🔴", VoteChoice.GENERIC, identifier="one", guild_id=11, label="One"),
    )
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10), VoteMessage(101, 201, 11)),
        options=options,
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    event = _event(mocker, event_type="REACTION_REMOVE", message_id=101, guild_id=11, emoji="🔴")
    actor = VoteActor(7, 70, guild_id=11)
    mocker.patch("squid.bot.voting.vote.resolve_actor", new=mocker.AsyncMock(return_value=actor))

    await cog.recover_reaction_remove(event)

    votes.cast_vote.assert_awaited_once_with(101, actor, "🔴")


@pytest.mark.parametrize("handler_name", ["on_reaction_clear", "on_reaction_clear_emoji"])
async def test_vote_adapter_restores_every_baseline_after_clear_and_tolerates_forbidden(
    handler_name: str,
    mocker: MockerFixture,
) -> None:
    session = poll_snapshot(messages=(VoteMessage(100, 200, 10),), options=GENERIC_OPTIONS)
    cog, bot, _votes = _cog(mocker, session)
    response = cast(Any, SimpleNamespace(status=403, reason="Forbidden"))
    message = mocker.Mock()
    message.add_reaction = mocker.AsyncMock(
        side_effect=[discord.Forbidden(response, "Cannot add reaction"), None]
    )
    bot.get_or_fetch_message.return_value = message
    event = SimpleNamespace(payload=SimpleNamespace(message_id=100), emoji=None)

    await getattr(cog, handler_name)(event)

    assert message.add_reaction.await_args_list == [
        mocker.call(GENERIC_OPTIONS[0].emoji),
        mocker.call(GENERIC_OPTIONS[1].emoji),
    ]


async def test_clear_recovery_restores_options_without_toggling_public_ballots(mocker: MockerFixture) -> None:
    session = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10),),
        options=GENERIC_OPTIONS,
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    cog, bot, votes = _cog(mocker, session)
    message = mocker.Mock()
    message.add_reaction = mocker.AsyncMock()
    bot.get_or_fetch_message.return_value = message
    event = SimpleNamespace(payload=SimpleNamespace(message_id=100), emoji=None)

    await cog.recover_reaction_clear(event)

    votes.cast_vote.assert_not_awaited()
    assert message.add_reaction.await_count == 2
