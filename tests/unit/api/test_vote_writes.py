"""HTTP vote mutation tests."""

from dataclasses import dataclass

import pytest

from squid.api.security import Caller
from squid.api.v1.votes import VoteInput, cast_vote
from squid.core.errors import AuthenticationError, AuthorizationError, ConflictError, ValidationError
from squid.voting.application import VoteService
from squid.voting.domain import CastVoteResult, VoteActor, VoteKind, VoteRejection, VoteSessionSnapshot
from squid.voting.errors import VoteSessionNotFoundError
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


@pytest.mark.parametrize("guild_id", [10, 999])
@pytest.mark.asyncio
async def test_vote_keeps_the_option_id_stable_across_guild_aliases(guild_id: int) -> None:
    session = snapshot()
    actor = VoteActor(1, 7, guild_id=guild_id, role_ids=frozenset({99}))
    votes = VoteRecorder(session)
    members = MemberRecorder(actor)

    response = await cast_vote(
        12,
        VoteInput(guild_id=guild_id, option_id="approve"),
        votes,
        members,
        account(),
    )

    assert members.calls == [(1, guild_id, VoteKind.BUILD)]
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


_REJECTION_ERRORS: dict[VoteRejection, type[Exception]] = {
    VoteRejection.NOT_FOUND: VoteSessionNotFoundError,
    VoteRejection.CLOSED: ConflictError,
    VoteRejection.NOT_ELIGIBLE: AuthorizationError,
    VoteRejection.INVALID_OPTION: ValidationError,
    VoteRejection.WRONG_GUILD: AuthorizationError,
    VoteRejection.NOT_AUTHORIZED: AuthorizationError,
}
assert set(_REJECTION_ERRORS) == set(VoteRejection)


@pytest.mark.parametrize(("rejection", "error_type"), _REJECTION_ERRORS.items())
@pytest.mark.asyncio
async def test_every_vote_rejection_maps_to_a_typed_api_error(
    rejection: VoteRejection,
    error_type: type[Exception],
) -> None:
    session = snapshot()
    votes = VoteRecorder(session, rejection=rejection)
    members = MemberRecorder(VoteActor(1, 7, guild_id=10))

    with pytest.raises(error_type):
        await cast_vote(
            12,
            VoteInput(guild_id=10, option_id="approve"),
            votes,
            members,
            account(f"account:{rejection.value}"),
        )
