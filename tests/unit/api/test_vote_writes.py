"""HTTP vote mutation tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.security import Principal, Scope
from squid.api.v1.votes import VoteInput, cast_vote
from squid.core.errors import AuthenticationError, ValidationError
from squid.runtime import ApiServices
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    CastVoteResult,
    VoteActor,
    VoteMessage,
    VoteSessionSnapshot,
    VoteTarget,
)


def snapshot() -> VoteSessionSnapshot:
    return VoteSessionSnapshot(
        id=12,
        author_account_id=7,
        kind="build",
        status="open",
        result="pending",
        pass_threshold=3,
        fail_threshold=-3,
        votes={},
        messages=(VoteMessage(100, 200, 10),),
        options=DEFAULT_VOTE_OPTIONS,
        target=VoteTarget(build_id=42),
    )


def account(subject: str = "account:1") -> Principal:
    return Principal(
        kind="account",
        subject=subject,
        scopes=frozenset({Scope.VOTES_CAST}),
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

    members.member.assert_awaited_once_with(1, 7, 10, "build")
    votes.cast_vote_by_session.assert_awaited_once_with(12, actor, "approve")
    assert response.id == 12


@pytest.mark.asyncio
async def test_service_credentials_cannot_cast_ballots() -> None:
    service = Principal(kind="service", subject="api-key:test", scopes=frozenset({Scope.VOTES_CAST}))

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
        cast_vote_by_session=AsyncMock(return_value=CastVoteResult(session, rejection="invalid_option")),
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
