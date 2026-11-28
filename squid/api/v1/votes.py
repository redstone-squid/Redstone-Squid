"""Ballot-safe vote-session routes."""

from litestar import Router, delete, get, post
from litestar.di import Provide
from litestar.params import FromPath, FromQuery, JSONBody
from pydantic import BaseModel, ConfigDict, Field

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB, WEB_WRITE, browser_only, contract, transport_only
from squid.api.dependencies import BuildQueries, CurrentCaller, Votes, get_build_queries, get_votes
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.security import requires
from squid.api.v1.schemas.votes import ReviewVoteSummary, VoteSessionDetail, VoteViewer
from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.errors import AuthorizationError, ConflictError, ValidationError
from squid.core.permissions.domain.catalogue import VOTE_POLL_CAST
from squid.core.voting.domain import (
    BuildEditVoteTarget,
    BuildVoteTarget,
    CastVoteResult,
    ReviewFilters,
    ReviewKind,
    ReviewSort,
    VoteRejection,
    VoteSessionSnapshot,
    VoteStatus,
)
from squid.core.voting.errors import VoteSessionNotFoundError

UserVoter = CurrentCaller


class VoteInput(BaseModel):
    """A stable option selection."""

    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=200)


@get(
    "/",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="vote_session_list",
    security=[WEB, DEVICE],
    opt=contract(cli=transport_only()),
)
async def list_review_votes(
    votes: Votes,
    build_queries: BuildQueries,
    caller: CurrentCaller,
    kind: FromQuery[list[ReviewKind] | None] = None,
    kind_any: FromQuery[list[ReviewKind] | None] = None,
    status: FromQuery[list[VoteStatus] | None] = None,
    sort: FromQuery[ReviewSort] = ReviewSort.NEWEST,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
) -> Page[ReviewVoteSummary]:
    """Page build submission and edit votes for reviewers; `status=open&sort=oldest` is the review queue.

    Each `kind` must match. Repeat `kind_any` to include several kinds.
    """
    account_id = caller.require_account_id()
    page = await votes.list_reviews(
        ReviewFilters(frozenset(kind_any or ()), tuple(status or ()), frozenset(kind or ())),
        sort=sort,
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
        context=caller.context(),
    )
    build_ids = [
        session.target.build_id
        for session in page.items
        if isinstance(session.target, BuildVoteTarget | BuildEditVoteTarget)
    ]
    builds = {build.id: build for build in await build_queries.get_many(build_ids, context=caller.context())}
    submitters = await build_queries.submitters(list(builds.values()), context=caller.context())

    def render(session: VoteSessionSnapshot) -> ReviewVoteSummary:
        detail = VoteSessionDetail.from_domain(session, caller_account_id=account_id)
        build = builds.get(detail.build_id) if detail.build_id is not None else None
        return ReviewVoteSummary(
            **dict(detail),
            build=None if build is None else PublicBuildSummary.from_build(build),
            submitted_by=None if build is None else submitters.get(build.id),
        )

    return Page[ReviewVoteSummary].from_domain(page, render)


@get(
    "/{vote_session_id:int}",
    responses=responses(404, 422, 503),
    operation_id="vote_session_get",
    security=[ANONYMOUS, SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only()),
)
async def get_vote_session(
    vote_session_id: FromPath[int],
    votes: Votes,
    caller: CurrentCaller,
) -> VoteSessionDetail:
    """Return aggregate vote state without exposing ballot identities.

    A signed-in caller with a consented account also sees whether they may vote and their weight.
    """
    session = await votes.get_session(vote_session_id, context=caller.context())
    if session is None:
        raise VoteSessionNotFoundError(vote_session_id)
    detail = VoteSessionDetail.from_domain(session, caller_account_id=caller.account_id)
    if caller.kind != "account" or caller.account_id is None or not session.is_open:
        return detail
    weight = await votes.base_weight(vote_session_id, context=caller.context())
    match weight:
        case VoteRejection.NOT_ELIGIBLE | VoteRejection.WRONG_GUILD:
            viewer = VoteViewer(eligible=False, weight=None)
        case float():
            viewer = VoteViewer(eligible=True, weight=weight)
        case _:
            viewer = None
    return detail.model_copy(update={"viewer": viewer})


@post(
    "/{vote_session_id:int}/votes",
    responses=responses(400, 401, 403, 404, 409, 429, 503),
    before_request=reserve_idempotent_before_request,
    guards=[requires(VOTE_POLL_CAST)],
    operation_id="vote_cast",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
)
async def cast_vote(
    vote_session_id: FromPath[int],
    data: JSONBody[VoteInput],
    votes: Votes,
    caller: UserVoter,
) -> VoteSessionDetail:
    """Cast the caller's vote, weighed from their roles in the session's owner guild.

    Repeating the call with the same option keeps the ballot.
    """
    account_id = caller.require_account_id()
    result = await votes.cast_vote(vote_session_id, data.option_id, toggle=False, context=caller.context())
    _raise_vote_rejection(vote_session_id, result)
    assert result.session is not None
    return VoteSessionDetail.from_domain(result.session, caller_account_id=account_id)


@delete(
    "/{vote_session_id:int}/votes",
    responses=responses(400, 401, 403, 404, 409, 429, 503),
    before_request=reserve_idempotent_before_request,
    guards=[requires(VOTE_POLL_CAST)],
    operation_id="vote_clear",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
)
async def clear_vote(
    vote_session_id: FromPath[int],
    votes: Votes,
    caller: UserVoter,
) -> VoteSessionDetail:
    """Remove the caller's ballot; repeating this keeps it absent."""
    account_id = caller.require_account_id()
    result = await votes.clear_vote(vote_session_id, context=caller.context())
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
            msg = "The option is not one of this session's options."
            raise ValidationError(msg, resource="vote")
        case VoteRejection.WRONG_GUILD | VoteRejection.NOT_ELIGIBLE | VoteRejection.NOT_AUTHORIZED:
            raise AuthorizationError


router = Router(
    opt={"x-squid-private-response": True},
    dependencies={
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
        "votes": Provide(get_votes, sync_to_thread=False),
    },
    path="/vote-sessions",
    tags=["vote sessions"],
    route_handlers=[list_review_votes, get_vote_session, cast_vote, clear_vote],
)
