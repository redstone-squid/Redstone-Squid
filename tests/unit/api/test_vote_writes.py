"""HTTP vote mutation tests."""

from dataclasses import dataclass

import pytest

from squid.api.security import Caller
from squid.api.v1.votes import VoteInput, cast_vote
from squid.core.errors import AuthenticationError, ValidationError
from squid.voting.application import VoteService
from squid.voting.domain import CastVoteResult, VoteActor, VoteKind, VoteRejection, VoteSessionSnapshot
from tests.support.voting import build_snapshot
from tests.unit.api.fakes import credential_nodes


def snapshot() -> VoteSessionSnapshot:
    return build_snapshot()


def account(subject: str = "account:1") -> Caller:
    return Caller(
        kind="account",
        subject=subject,
        nodes=credential_nodes("vote.poll.cast"),
        account_id=1,
    )


@dataclass(frozen=True)
class CastCall:
    session_id: int
    actor: VoteActor
    option_id: str


class VoteRecorder(VoteService):
    def __init__(self, session: VoteSessionSnapshot, *, rejection: VoteRejection | None = None) -> None:
        self.session = session
        self.rejection = rejection
        self.cast_calls: list[CastCall] = []

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        assert vote_session_id == 12
        return self.session

    async def cast_vote_by_session(self, vote_session_id: int, actor: VoteActor, option_id: str) -> CastVoteResult:
        self.cast_calls.append(CastCall(vote_session_id, actor, option_id))
        return CastVoteResult(self.session, rejection=self.rejection)


class MemberRecorder:
    def __init__(self, actor: VoteActor) -> None:
        self.actor = actor
        self.calls: list[tuple[int, int, VoteKind]] = []

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        self.calls.append((account_id, guild_id, kind))
        return self.actor

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        return await self.member(account_id, guild_id, kind)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_vote_resolves_current_guild_membership_and_casts_by_option_id() -> None:
    session = snapshot()
    actor = VoteActor(1, 7, guild_id=10, role_ids=frozenset({99}))
    votes = VoteRecorder(session)
    members = MemberRecorder(actor)

    response = await cast_vote(
        12,
        VoteInput(guild_id=10, option_id="approve"),
        votes,
        members,
        account(),
    )

    assert members.calls == [(1, 10, VoteKind.BUILD)]
    assert votes.cast_calls == [CastCall(12, actor, "approve")]
    assert response.id == 12


@pytest.mark.asyncio
async def test_a_cli_caller_with_no_discord_identity_can_vote() -> None:
    """Refused before: the gate demanded a snowflake nothing on this path used."""
    session = snapshot()
    actor = VoteActor(1, 7, guild_id=10, role_ids=frozenset({99}))
    votes = VoteRecorder(session)
    members = MemberRecorder(actor)
    cli = Caller(
        kind="cli",
        subject="account:1",
        nodes=credential_nodes("vote.poll.cast"),
        account_id=1,
    )

    response = await cast_vote(12, VoteInput(guild_id=10, option_id="approve"), votes, members, cli)

    assert response.id == 12
    assert votes.cast_calls == [CastCall(12, actor, "approve")]


@pytest.mark.asyncio
async def test_service_credentials_cannot_cast_ballots() -> None:
    service = Caller(kind="service", subject="api-key:test", nodes=credential_nodes("vote.poll.cast"))

    with pytest.raises(AuthenticationError):
        await cast_vote(12, VoteInput(guild_id=10, option_id="approve"), VoteRecorder(snapshot()), None, service)


@pytest.mark.asyncio
async def test_invalid_option_is_a_typed_client_error() -> None:
    session = snapshot()
    votes = VoteRecorder(session, rejection=VoteRejection.INVALID_OPTION)
    members = MemberRecorder(VoteActor(1, 7, guild_id=10))

    with pytest.raises(ValidationError):
        await cast_vote(
            12,
            VoteInput(guild_id=10, option_id="missing"),
            votes,
            members,
            account("account:invalid-option"),
        )
