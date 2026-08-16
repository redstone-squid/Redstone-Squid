"""Ballot-safe vote-session routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from squid.accounts.errors import ConsentRequiredError
from squid.api.dependencies import CurrentPrincipal, VoteMembers, Votes
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.security import Principal, requires
from squid.api.v1.schemas.votes import VoteSessionDetail
from squid.core.errors import AuthenticationError, AuthorizationError, ConflictError, ValidationError
from squid.permissions.domain.catalogue import VOTE_POLL_CAST
from squid.voting.domain import CastVoteResult, VoteRejection
from squid.voting.errors import VoteSessionNotFoundError

router = APIRouter(prefix="/vote-sessions", tags=["vote sessions"])
UserVoter = Annotated[Principal, Depends(requires(VOTE_POLL_CAST))]


class VoteInput(BaseModel):
    """A stable option selection in one Discord guild."""

    model_config = ConfigDict(extra="forbid")

    guild_id: int = Field(gt=0)
    option_id: str = Field(min_length=1, max_length=200)


@router.get("/{vote_session_id}", response_model=VoteSessionDetail, responses=responses(404, 422, 503))
async def get_vote_session(
    vote_session_id: int,
    votes: Votes,
    principal: CurrentPrincipal,
) -> VoteSessionDetail:
    """Return aggregate vote state without exposing ballot identities."""
    session = await votes.get_session_by_id(vote_session_id)
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    return VoteSessionDetail.from_domain(session, caller_account_id=principal.account_id)


@router.post(
    "/{vote_session_id}/votes",
    response_model=VoteSessionDetail,
    responses=responses(400, 401, 403, 404, 409, 429, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def cast_vote(
    vote_session_id: int,
    vote: VoteInput,
    votes: Votes,
    vote_members: VoteMembers,
    principal: UserVoter,
) -> VoteSessionDetail:
    """Cast the authenticated Discord member's weighted vote."""
    if principal.kind != "account" or principal.account_id is None or principal.discord_id is None:
        raise AuthenticationError
    if principal.consent_pending:
        raise ConsentRequiredError(principal.discord_id, account_id=principal.account_id).with_context(
            public_context={"consent_url": "/v1/users/me/consent"},
            end_user_action="Accept the current privacy notice and retry.",
        )
    session = await votes.get_session_by_id(vote_session_id)
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    if vote_members is None:
        raise AuthorizationError
    actor = await vote_members.member(principal.account_id, principal.discord_id, vote.guild_id, session.kind)
    if actor is None:
        raise AuthorizationError
    result = await votes.cast_vote_by_session(vote_session_id, actor, vote.option_id)
    _raise_vote_rejection(vote_session_id, result)
    assert result.session is not None
    return VoteSessionDetail.from_domain(result.session, caller_account_id=principal.account_id)


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
