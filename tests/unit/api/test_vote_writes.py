"""HTTP vote mutation tests."""

from dataclasses import dataclass
from typing import override

import pytest

from squid.api.security import Caller
from squid.api.v1.votes import VoteInput, cast_vote, clear_vote
from squid.core.actors import ActingContext, Actor
from squid.core.errors import AuthenticationError, AuthorizationError, ConflictError, ValidationError
from squid.core.voting.application import VoteService
from squid.core.voting.domain import (
    DEFAULT_VOTE_MENU,
    CastVoteResult,
    VoteAlias,
    VoteMenu,
    VoteMessage,
    VoteRejection,
    VoteSessionSnapshot,
)
from squid.core.voting.errors import VoteSessionNotFoundError
from tests.support.voting import build_snapshot
from tests.unit.api.fakes import credential_nodes


def snapshot() -> VoteSessionSnapshot:
    return build_snapshot(
        messages=(VoteMessage(100, 200, 10), VoteMessage(101, 201, 999)),
        menu=VoteMenu(
            DEFAULT_VOTE_MENU.options,
            (
                VoteAlias("approve", "<:yes:1>", 10),
                VoteAlias("deny", "<:no:2>", 10, 1),
                VoteAlias("approve", "✅", 999),
                VoteAlias("deny", "❌", 999, 1),
            ),
        ),
    )


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
    actor: Actor
    option_id: str


class VoteRecorder(VoteService):
    def __init__(self, session: VoteSessionSnapshot, *, rejection: VoteRejection | None = None) -> None:
        self.session = session
        self.rejection = rejection
        self.cast_calls: list[CastCall] = []

    @override
    async def get_session(self, vote_session_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        assert vote_session_id == 12
        return self.session

    @override
    async def cast_vote(
        self,
        vote_session_id: int,
        option_id: str,
        *,
        emoji: str | None = None,
        toggle: bool = True,
        count_revision: int | None = None,
        context: ActingContext,
    ) -> CastVoteResult:
        actor = context.effective
        assert emoji is None
        assert not toggle
        self.cast_calls.append(CastCall(vote_session_id, actor, option_id))
        return CastVoteResult(self.session, rejection=self.rejection)

    @override
    async def clear_vote(self, vote_session_id: int, *, context: ActingContext) -> CastVoteResult:
        actor = context.effective
        self.cast_calls.append(CastCall(vote_session_id, actor, "clear"))
        return CastVoteResult(self.session, rejection=self.rejection)


@pytest.mark.asyncio
async def test_a_web_vote_names_only_the_option() -> None:
    """No guild is involved: the service weighs the ballot from the owner guild's stored roles."""
    votes = VoteRecorder(snapshot())

    response = await cast_vote.fn(12, VoteInput(option_id="approve"), votes, account())

    assert votes.cast_calls == [CastCall(12, account().context().effective, "approve")]
    assert response.id == 12


@pytest.mark.asyncio
async def test_a_cli_caller_with_no_discord_identity_can_vote() -> None:
    votes = VoteRecorder(snapshot())
    cli = Caller(kind="cli", subject="account:1", nodes=credential_nodes("vote.poll.cast"), account_id=1)

    response = await cast_vote.fn(12, VoteInput(option_id="approve"), votes, cli)

    assert response.id == 12
    assert votes.cast_calls == [CastCall(12, cli.context().effective, "approve")]


@pytest.mark.asyncio
async def test_service_credentials_cannot_cast_ballots() -> None:
    service = Caller(kind="service", subject="api-key:test", nodes=credential_nodes("vote.poll.cast"))

    with pytest.raises(AuthenticationError):
        await cast_vote.fn(12, VoteInput(option_id="approve"), VoteRecorder(snapshot()), service)


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

    with pytest.raises(error_type):
        await cast_vote.fn(12, VoteInput(option_id="approve"), votes, account(f"account:{rejection.value}"))


async def test_clear_acts_as_the_authenticated_caller() -> None:
    votes = VoteRecorder(snapshot())
    await clear_vote.fn(12, votes, account())
    assert votes.cast_calls == [CastCall(12, account().context().effective, "clear")]
