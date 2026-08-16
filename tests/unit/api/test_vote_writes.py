"""HTTP vote mutation tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.security import Caller
from squid.api.v1.votes import VoteInput, cast_vote
from squid.core.errors import AuthenticationError, ValidationError
from squid.runtime import ApiServices
from squid.voting.domain import CastVoteResult, VoteActor, VoteRejection, VoteSessionSnapshot
from tests.helpers.voting import build_snapshot
from tests.unit.api.fakes import credential_nodes


def snapshot() -> VoteSessionSnapshot:
    return build_snapshot()


def account(subject: str = "account:1") -> Caller:
    return Caller(
        kind="account",
        subject=subject,
        nodes=credential_nodes("vote.poll.cast"),
        discord_id=7,
        account_id=1,
    )


@pytest.mark.asyncio
async def test_vote_resolves_current_guild_membership_and_casts_by_option_id() -> None:
    session = snapshot()
    actor = VoteActor(1, 7, guild_id=10, role_ids=frozenset({99}))
    votes = SimpleNamespace(
        get_session_by_id=AsyncMock(return_value=session),
        cast_vote_by_session=AsyncMock(return_value=CastVoteResult(session)),
    )
    members = SimpleNamespace(member=AsyncMock(return_value=actor))
    services = cast(ApiServices, SimpleNamespace(votes=votes, vote_members=members))

    response = await cast_vote(
        12,
        VoteInput(guild_id=10, option_id="approve"),
        services.votes,
        cast(Any, members),
        account(),
    )

    members.member.assert_awaited_once_with(1, 10, "build")
    votes.cast_vote_by_session.assert_awaited_once_with(12, actor, "approve")
    assert response.id == 12


@pytest.mark.asyncio
async def test_service_credentials_cannot_cast_ballots() -> None:
    service = Caller(kind="service", subject="api-key:test", nodes=credential_nodes("vote.poll.cast"))

    with pytest.raises(AuthenticationError):
        await cast_vote(
            12,
            VoteInput(guild_id=10, option_id="approve"),
            cast(Any, SimpleNamespace()),
            None,
            service,
        )


@pytest.mark.asyncio
async def test_invalid_option_is_a_typed_client_error() -> None:
    session = snapshot()
    votes = SimpleNamespace(
        get_session_by_id=AsyncMock(return_value=session),
        cast_vote_by_session=AsyncMock(return_value=CastVoteResult(session, rejection=VoteRejection.INVALID_OPTION)),
    )
    members = SimpleNamespace(member=AsyncMock(return_value=VoteActor(1, 7, guild_id=10)))

    with pytest.raises(ValidationError):
        await cast_vote(
            12,
            VoteInput(guild_id=10, option_id="missing"),
            cast(Any, votes),
            cast(Any, members),
            account("account:invalid-option"),
        )
