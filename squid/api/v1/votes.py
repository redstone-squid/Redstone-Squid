"""Ballot-safe vote-session routes."""

from litestar import Router, delete, get, post
from litestar.di import Provide
from litestar.params import FromPath, FromQuery, JSONBody
from pydantic import BaseModel, ConfigDict, Field

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import CurrentCaller, VoteMembers, Votes
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.security import requires
from squid.api.v1.schemas.votes import VoteSessionDetail
from squid.core.errors import AuthorizationError, ConflictError, ValidationError
from squid.permissions.domain.catalogue import VOTE_POLL_CAST
from squid.voting.domain import CastVoteResult, VoteRejection
from squid.voting.errors import VoteSessionNotFoundError

UserVoter = CurrentCaller


class VoteInput(BaseModel):
    """A stable option selection in one Discord guild."""

    model_config = ConfigDict(extra="forbid")

    guild_id: int = Field(gt=0)
    option_id: str = Field(min_length=1, max_length=200)


@get(
    "/{vote_session_id:int}",
    responses=responses(404, 422, 503),
    operation_id="vote_session_get",
    opt=contract(security=[ANONYMOUS, SERVICE, WEB, DEVICE], cli=transport_only()),
)
async def get_vote_session(
    vote_session_id: FromPath[int],
    votes: Votes,
    caller: CurrentCaller,
) -> VoteSessionDetail:
    """Return aggregate vote state without exposing ballot identities."""
    session = await votes.get_session(vote_session_id)
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    return VoteSessionDetail.from_domain(session, caller_account_id=caller.account_id)


@post(
    "/{vote_session_id:int}/votes",
    responses=responses(400, 401, 403, 404, 409, 429, 503),
    before_request=reserve_idempotent_before_request,
    dependencies={"caller": Provide(requires(VOTE_POLL_CAST))},
    operation_id="vote_cast",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
)
async def cast_vote(
    vote_session_id: FromPath[int],
    data: JSONBody[VoteInput],
    votes: Votes,
    vote_members: VoteMembers,
    caller: UserVoter,
) -> VoteSessionDetail:
    """Cast the authenticated Discord member's weighted vote."""
    account_id = caller.require_consented_account()
    session = await votes.get_session(vote_session_id)
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    if vote_members is None:
        raise AuthorizationError
    actor = await vote_members.member(account_id, data.guild_id, session.kind)
    if actor is None:
        raise AuthorizationError
    result = await votes.cast_vote(vote_session_id, actor, data.option_id, toggle=False)
    _raise_vote_rejection(vote_session_id, result)
    assert result.session is not None
    return VoteSessionDetail.from_domain(result.session, caller_account_id=account_id)


@delete(
    "/{vote_session_id:int}/votes",
    responses=responses(400, 401, 403, 404, 409, 429, 503),
    before_request=reserve_idempotent_before_request,
    dependencies={"caller": Provide(requires(VOTE_POLL_CAST))},
    operation_id="vote_clear",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
)
async def clear_vote(
    vote_session_id: FromPath[int],
    guild_id: FromQuery[int],
    votes: Votes,
    vote_members: VoteMembers,
    caller: UserVoter,
) -> VoteSessionDetail:
    """Remove the authenticated member's ballot; repeating this keeps it absent."""
    account_id = caller.require_consented_account()
    session = await votes.get_session(vote_session_id)
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    if vote_members is None:
        raise AuthorizationError
    actor = await vote_members.member(account_id, guild_id, session.kind)
    if actor is None:
        raise AuthorizationError
    result = await votes.clear_vote(vote_session_id, actor)
    _raise_vote_rejection(vote_session_id, result)
    assert result.session is not None
    return VoteSessionDetail.from_domain(result.session, caller_account_id=account_id)


def _raise_vote_rejection(vote_session_id: int, result: CastVoteResult) -> None:
    match result.rejection:
        case None:
            return
        case VoteRejection.NOT_FOUND:
            raise VoteSessionNotFoundError(vote_session_id)
        case VoteRejection.CLOSED:
            msg = "The vote session is closed."
            raise ConflictError(msg, resource="vote_session")
        case VoteRejection.INVALID_OPTION:
            msg = "The option is not available in this guild."
            raise ValidationError(msg, resource="vote")
        case VoteRejection.WRONG_GUILD | VoteRejection.NOT_ELIGIBLE | VoteRejection.NOT_AUTHORIZED:
            raise AuthorizationError


router = Router(path="/vote-sessions", tags=["vote sessions"], route_handlers=[get_vote_session, cast_vote, clear_vote])
