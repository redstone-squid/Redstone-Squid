"""The build review queue joins each vote to the build it decides."""

from collections.abc import Sequence
from typing import override
from uuid import UUID

from squid.api.security import Caller
from squid.api.v1.votes import list_review_votes
from squid.core.actors import ActingContext
from squid.core.builds.application import BuildQueryService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.history import HistoryActor
from squid.core.builds.models import Build, DoorBuild
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.voting.application import VoteService
from squid.core.voting.domain import (
    BuildVoteTarget,
    ReviewFilters,
    ReviewKind,
    ReviewSort,
    VoteSelection,
    VoteSessionSnapshot,
    VoteStatus,
)
from tests.support.voting import build_snapshot
from tests.unit.api.fakes import credential_nodes

SUBMITTER = HistoryActor("Builder", UUID(int=5))


class ReviewVotes(VoteService):
    def __init__(self, *sessions: VoteSessionSnapshot) -> None:
        self.sessions = sessions
        self.filters: list[ReviewFilters] = []

    @override
    async def list_reviews(
        self,
        filters: ReviewFilters,
        *,
        sort: ReviewSort = ReviewSort.NEWEST,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[VoteSessionSnapshot]:
        self.filters.append(filters)
        return Page(self.sessions, len(self.sessions), None, None)


class PendingBuilds(BuildQueryService):
    def __init__(self) -> None:
        pass

    @override
    async def get_many(self, build_ids: Sequence[int], *, context: ActingContext) -> list[Build]:
        build = DoorBuild(
            id=42,
            submitter_account_id=5,
            submission_status=BuildStatus.PENDING,
            versions=["1.21"],
            door_width=2,
            door_height=2,
            patterns=["Regular"],
            orientation="Door",
        )
        return [build] if 42 in build_ids else []

    @override
    async def submitters(self, builds: Sequence[Build], *, context: ActingContext) -> dict[int, HistoryActor]:
        return {build.id: SUBMITTER for build in builds}


def reviewer() -> Caller:
    return Caller(kind="account", subject="account:7", nodes=credential_nodes("*"), account_id=7)


async def test_review_votes_carry_the_build_its_submitter_and_the_callers_ballot() -> None:
    session = build_snapshot(selections=(VoteSelection(7, 10, "approve", "✅", 1.0),))
    votes = ReviewVotes(session, build_snapshot(id=13, target=BuildVoteTarget(2)))

    page = await list_review_votes.fn(
        votes, PendingBuilds(), reviewer(), kind=[ReviewKind.BUILD], status=VoteStatus.OPEN, sort=ReviewSort.OLDEST
    )

    first, deleted = page.items
    assert votes.filters == [ReviewFilters(frozenset({ReviewKind.BUILD}), VoteStatus.OPEN)]
    assert first.build is not None
    assert (first.build.id, first.submitted_by) == (42, SUBMITTER)
    assert first.own_selection is not None
    assert first.own_selection.option_id == "approve"
    assert (deleted.build, deleted.submitted_by) == (None, None)
