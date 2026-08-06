"""Ballot-safe vote-session read routes."""

from fastapi import APIRouter

from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.v1.schemas.votes import VoteSessionDetail
from squid.core.errors import NotFoundError

router = APIRouter(prefix="/vote-sessions", tags=["vote sessions"])


@router.get("/{vote_session_id}", response_model=VoteSessionDetail, responses=responses(404, 422, 503))
async def get_vote_session(vote_session_id: int, services: Services) -> VoteSessionDetail:
    """Return aggregate vote state without exposing ballot identities."""
    session = await services.votes.get_session_by_id(vote_session_id)
    if session is None:
        msg = "Vote session not found."
        raise NotFoundError(
            msg,
            resource="vote_session",
            public_context={"vote_session_id": vote_session_id},
        )
    return VoteSessionDetail.from_domain(session)
