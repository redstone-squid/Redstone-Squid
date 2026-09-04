"""Discord reaction adaptation for vote sessions, independent of starboard."""

from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from pytest_mock import MockerFixture

from squid.bot.voting.vote import VoteCog
from squid.voting.domain import CastVoteResult, VoteActor, VoteMessage, VoteSelection, VoteVisibility
from tests.support.discord import make_reaction_payload
from tests.support.voting import GENERIC_OPTIONS, poll_snapshot


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


def _event(mocker: MockerFixture, *, event_type: str = "REACTION_ADD") -> Any:
    payload = make_reaction_payload(
        message_id=100,
        channel_id=200,
        guild_id=10,
        user_id=70,
        emoji="1️⃣",
        event_type=cast(Any, event_type),
    )
    return SimpleNamespace(
        payload=payload,
        emoji=str(payload.emoji),
        message=mocker.AsyncMock(return_value=mocker.Mock()),
        resolve_member=mocker.AsyncMock(return_value=mocker.Mock(bot=False)),
    )


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
