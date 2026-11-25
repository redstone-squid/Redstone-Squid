"""Authoritative build collection views: moderation status and submitter ownership."""

from dataclasses import dataclass
from typing import Any, NamedTuple, cast, override

import pytest

from squid.api.pagination import PageAnchor
from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.api.v1.builds import list_builds
from squid.api.v1.me import list_my_builds
from squid.api.v1.schemas.builds import BuildStatusFilter
from squid.core.actors import ActingContext, Actor
from squid.core.builds.application import DEFAULT_BUILD_LIST_SORT, BuildListSort, BuildQueryService
from squid.core.builds.domain import EMPTY_BUILD_FILTERS, BuildListFilters, BuildStatus
from squid.core.builds.models import Build, DoorBuild
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.domain import CheckMode, Decision, PermissionCheck, PermissionNode, Reason
from squid.core.permissions.domain.models import RolePermissionSubject
from tests.unit.api.fakes import credential_nodes

# This credential can read submissions but does not carry a moderation-view permission.
SERVICE = Caller(
    kind="service",
    subject="api-key:test",
    nodes=credential_nodes("build.submission.read", "build.submission.create"),
)
ACCOUNT = Caller(kind="account", subject="account:1", nodes=UNBOUNDED, account_id=1)


@dataclass(frozen=True)
class ListPageCall:
    statuses: frozenset[BuildStatus]
    submitter_account_id: int | None
    sort: BuildListSort
    selector: PageSelector
    page_size: int


class BuildQueryRecorder(BuildQueryService):
    """Record visibility filters and paginate supplied rows without applying those filters."""

    def __init__(self, builds: list[Build]) -> None:
        self.builds = builds
        self.calls: list[ListPageCall] = []

    @override
    async def list_page(
        self,
        *,
        context: ActingContext,
        statuses: frozenset[BuildStatus],
        filters: BuildListFilters = EMPTY_BUILD_FILTERS,
        submitter_account_id: int | None = None,
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        self.calls.append(ListPageCall(statuses, submitter_account_id, sort, selector, page_size))
        return keyset_page(
            self.builds,
            selector=selector,
            page_size=page_size,
            total=len(self.builds),
            id_of=lambda build: build.id or 0,
        )


class PermissionRecorder(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[Actor | RolePermissionSubject, PermissionNode | str]] = []

    @override
    async def check(
        self,
        subject: Actor | RolePermissionSubject,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        self.calls.extend((subject, node) for node in nodes)
        return PermissionCheck(
            tuple(Decision(str(node), allowed=self.allowed, reason=Reason.DEFAULT) for node in nodes), mode=mode
        )


class Fakes(NamedTuple):
    """The concrete service subclasses an authoritative build route drives."""

    build_queries: BuildQueryRecorder
    permissions: PermissionRecorder


def persisted_build(build_id: int, status: BuildStatus = BuildStatus.PENDING) -> Build:
    return DoorBuild(
        id=build_id,
        submitter_account_id=123,
        submission_status=status,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        patterns=["Regular"],
        orientation="Door",
    )


def fakes(*, builds: list[Build] | None = None, is_admin: bool = False) -> Fakes:
    """Build concrete service subclasses for authoritative build routes."""
    return Fakes(BuildQueryRecorder(builds or []), PermissionRecorder(is_admin))


@pytest.mark.asyncio
async def test_anonymous_status_filter_defaults_to_approved_only() -> None:
    graph = fakes(builds=[persisted_build(1, BuildStatus.APPROVED)])

    await list_builds.fn(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ANONYMOUS,
    )

    assert graph.build_queries.calls[-1].statuses == frozenset({BuildStatus.APPROVED})


@pytest.mark.asyncio
async def test_pending_view_requires_an_authenticated_human() -> None:
    graph = fakes()

    with pytest.raises(AuthenticationError):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ANONYMOUS,
            status=BuildStatusFilter.PENDING,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_a_service_key_without_the_node_cannot_read_unreviewed_submissions() -> None:
    """The credential boundary must refuse access even when the permission service allows it."""
    graph = fakes(is_admin=True)

    with pytest.raises(AuthorizationError):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            SERVICE,
            status=BuildStatusFilter.REJECTED,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_a_key_carrying_the_node_is_still_bounded_by_its_owner() -> None:
    """An unbounded credential still requires permission from its owning account."""
    graph = fakes(is_admin=False)
    owned = Caller(kind="service", subject="api-key:owned", nodes=UNBOUNDED, account_id=1)

    with pytest.raises(AuthorizationError):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            owned,
            status=BuildStatusFilter.REJECTED,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_non_administrator_user_cannot_read_unreviewed_submissions() -> None:
    graph = fakes(is_admin=False)

    with pytest.raises(AuthorizationError):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            status=BuildStatusFilter.PENDING,
        )


@pytest.mark.asyncio
async def test_administrator_reads_the_pending_queue() -> None:
    graph = fakes(builds=[persisted_build(9)], is_admin=True)

    page = await list_builds.fn(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        status=BuildStatusFilter.PENDING,
    )

    assert graph.build_queries.calls[-1].statuses == frozenset({BuildStatus.PENDING})
    assert [item.id for item in page.items] == [9]


@pytest.mark.asyncio
async def test_searching_unpublished_builds_requires_review_permission() -> None:
    graph = fakes()

    with pytest.raises(AuthorizationError):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            q="piston",
            status=BuildStatusFilter.PENDING,
        )


@pytest.mark.asyncio
async def test_id_anchors_address_the_pages_on_either_side() -> None:
    builds = [persisted_build(9, BuildStatus.APPROVED), persisted_build(8, BuildStatus.APPROVED)]
    graph = fakes(builds=builds, is_admin=True)

    first = await list_builds.fn(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        page_size=1,
    )

    assert [item.id for item in first.items] == [9]
    assert first.next == PageAnchor(after_id=9)
    assert first.prev is None

    back = await list_builds.fn(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        page_size=1,
        before_id=8,
    )

    assert graph.build_queries.calls[-1].selector.before_id == 8
    assert back.prev is not None
    assert back.next == PageAnchor(after_id=8)


@pytest.mark.asyncio
async def test_pagination_parameters_are_mutually_exclusive() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="cannot be combined"):
        await list_builds.fn(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            offset=20,
            after_id=9,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_id_anchors_page_a_named_sort() -> None:
    graph = fakes(is_admin=True)

    await list_builds.fn(graph.build_queries, cast(Any, None), graph.permissions, ACCOUNT, sort="newest", after_id=9)

    call = graph.build_queries.calls[-1]
    assert call.sort == BuildListSort(field="submission_time", descending=True)
    assert call.selector == PageSelector(after_id=9)


@pytest.mark.asyncio
async def test_a_time_sorted_listing_pages_by_offset() -> None:
    graph = fakes(builds=[persisted_build(9, BuildStatus.APPROVED)], is_admin=True)

    page = await list_builds.fn(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        sort="oldest",
        page_size=1,
        offset=20,
    )

    call = graph.build_queries.calls[-1]
    assert call.sort == BuildListSort(field="submission_time", descending=False)
    assert call.selector.offset == 20
    assert page.prev == PageAnchor(offset=19)


@pytest.mark.asyncio
async def test_submitters_see_their_own_builds_in_every_status() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4, BuildStatus.REJECTED)])

    page = await list_my_builds.fn(graph.build_queries, ACCOUNT)

    call = graph.build_queries.calls[-1]
    assert call.statuses == frozenset(BuildStatus)
    assert call.submitter_account_id == 1
    assert [item.id for item in page.items] == [5, 4]


@pytest.mark.asyncio
async def test_an_account_submitter_can_list_builds_without_discord() -> None:
    graph = fakes(builds=[persisted_build(5)])
    minecraft_only = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)

    page = await list_my_builds.fn(graph.build_queries, minecraft_only)

    assert graph.build_queries.calls[-1].submitter_account_id == 7
    assert [item.id for item in page.items] == [5]


@pytest.mark.asyncio
async def test_own_build_anchors_stay_scoped_to_the_submitter() -> None:
    """The route must pass the caller's ownership filter alongside an arbitrary anchor.

    The recorder checks the query arguments; it does not enforce repository row visibility.
    """
    graph = fakes(builds=[persisted_build(5)])
    other = Caller(kind="account", subject="account:2", nodes=UNBOUNDED, account_id=2)

    await list_my_builds.fn(graph.build_queries, other, after_id=9)

    call = graph.build_queries.calls[-1]
    assert call.selector.after_id == 9
    assert call.submitter_account_id == 2


@pytest.mark.asyncio
async def test_own_builds_reject_a_service_credential() -> None:
    with pytest.raises(AuthenticationError):
        await list_my_builds.fn(fakes().build_queries, SERVICE)
