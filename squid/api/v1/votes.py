"""Ballot-safe vote-session routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from squid.api.dependencies import CurrentPrincipal, Services
from squid.api.errors import responses
from squid.api.rate_limit import SlidingWindowRateLimiter
from squid.api.security import Principal, Scope, require
from squid.api.v1.schemas.votes import VoteSessionDetail
from squid.core.errors import AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.users.errors import ConsentRequiredError
from squid.voting.domain import CastVoteResult

router = APIRouter(prefix="/vote-sessions", tags=["vote sessions"])
_vote_limiter = SlidingWindowRateLimiter(30, 300)
UserVoter = Annotated[Principal, Depends(require(Scope.VOTES_CAST))]


class VoteInput(BaseModel):
    """A stable option selection in one Discord guild."""

    model_config = ConfigDict(extra="forbid")

    guild_id: int = Field(gt=0)
    option_id: str = Field(min_length=1, max_length=200)


@router.get("/{vote_session_id}", response_model=VoteSessionDetail, responses=responses(404, 422, 503))
async def get_vote_session(
    vote_session_id: int,
    services: Services,
    principal: CurrentPrincipal,
) -> VoteSessionDetail:
    """Return aggregate vote state without exposing ballot identities."""
    session = await services.votes.get_session_by_id(vote_session_id)
    if session is None:
        msg = "Vote session not found."
        raise NotFoundError(
            msg,
            resource="vote_session",
            public_context={"vote_session_id": vote_session_id},
        )
    return VoteSessionDetail.from_domain(session, caller_id=principal.discord_id)


@router.post(
    "/{vote_session_id}/votes", response_model=VoteSessionDetail, responses=responses(400, 401, 403, 404, 409, 429, 503)
)
async def cast_vote(
    vote_session_id: int,
    vote: VoteInput,
    services: Services,
    principal: UserVoter,
) -> VoteSessionDetail:
    """Cast the authenticated Discord member's weighted vote."""
    if principal.kind != "user" or principal.discord_id is None:
        raise AuthenticationError
    if principal.consent_pending:
        raise ConsentRequiredError(principal.discord_id).with_context(
            public_context={"consent_url": "/v1/users/me/consent"},
            end_user_action="Accept the current privacy notice and retry.",
        )
    await _vote_limiter.check(principal.subject)
    session = await services.votes.get_session_by_id(vote_session_id)
    if session is None:
        raise _vote_not_found(vote_session_id)
    if services.vote_members is None:
        raise AuthorizationError
    actor = await services.vote_members.member(principal.discord_id, vote.guild_id, session.kind)
    if actor is None:
        raise AuthorizationError
    result = await services.votes.cast_vote_by_session(vote_session_id, actor, vote.option_id)
    _raise_vote_rejection(vote_session_id, result)
    assert result.session is not None
    return VoteSessionDetail.from_domain(result.session, caller_id=principal.discord_id)


def _raise_vote_rejection(vote_session_id: int, result: CastVoteResult) -> None:
    match result.rejection:
        case None:
            return
        case "not_found":
            raise _vote_not_found(vote_session_id)
        case "closed":
            msg = "The vote session is closed."
            raise ConflictError(msg, resource="vote_session")
        case "invalid_option":
            msg = "The option is not available in this guild."
            raise ValidationError(msg, resource="vote")
        case "wrong_guild" | "not_eligible" | "not_authorized":
            raise AuthorizationError


def _vote_not_found(vote_session_id: int) -> NotFoundError:
    msg = "Vote session not found."
    return NotFoundError(
        msg,
        resource="vote_session",
        public_context={"vote_session_id": vote_session_id},
    )
