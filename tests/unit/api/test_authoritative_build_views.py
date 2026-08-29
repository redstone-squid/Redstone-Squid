"""Authoritative build collection views: moderation status and submitter ownership."""

from dataclasses import dataclass
from typing import Any, NamedTuple, cast

import pytest

from squid.api.pagination import PageAnchor
from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.api.v1.builds import list_builds
from squid.api.v1.me import list_my_builds
from squid.api.v1.schemas.builds import BuildStatusFilter
from squid.builds.application import DEFAULT_BUILD_LIST_SORT, BuildListSort, BuildQueryService
from squid.builds.domain import Build, DoorBuild, Status
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.permissions.application import PermissionService
from squid.permissions.domain import PermissionNode, Subject
from tests.unit.api.fakes import credential_nodes

# A realistic key: the scopes a service credential is actually issued, which do
# not include the moderation views.
SERVICE = Caller(
    kind="service",
    subject="api-key:test",
    nodes=credential_nodes("build.submission.read", "build.submission.create"),
)
ACCOUNT = Caller(kind="account", subject="account:1", nodes=UNBOUNDED, account_id=1)


@dataclass(frozen=True)
class ListPageCall:
    statuses: frozenset[Status]
    submitter_account_id: int | None
    sort: BuildListSort
    selector: PageSelector
    page_size: int


class BuildQueryRecorder(BuildQueryService):
    def __init__(self, builds: list[Build]) -> None:
        self.builds = builds
        self.calls: list[ListPageCall] = []

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
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
            keyset=sort.field == "id",
            id_of=lambda build: build.id or 0,
        )


class PermissionRecorder(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[Subject, PermissionNode | str]] = []

    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        self.calls.append((subject, node))
        return self.allowed


class Fakes(NamedTuple):
    """The concrete service subclasses an authoritative build route drives."""

    build_queries: BuildQueryRecorder
    permissions: PermissionRecorder


def persisted_build(build_id: int, status: Status = Status.PENDING) -> Build:
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
async def test_anonymous_status_filter_defaults_to_confirmed_only() -> None:
    graph = fakes(builds=[persisted_build(1, Status.CONFIRMED)])

    await list_builds(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ANONYMOUS,
    )

    assert graph.build_queries.calls[-1].statuses == frozenset({Status.CONFIRMED})


@pytest.mark.asyncio
async def test_pending_view_requires_an_authenticated_human() -> None:
    graph = fakes()

    with pytest.raises(AuthenticationError):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ANONYMOUS,
            status=BuildStatusFilter.PENDING,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_a_service_key_without_the_node_cannot_read_unreviewed_submissions() -> None:
    """The property is kept, but as policy rather than as a hardcoded branch.

    No key is issued `build.submission.view_pending`, so a leaked key still
    reads nothing unreviewed -- and a key that genuinely should read them can now
    be given one, which the old `kind != "account"` branch made impossible.
    """
    graph = fakes(is_admin=True)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            SERVICE,
            status=BuildStatusFilter.DENIED,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_a_key_carrying_the_node_is_still_bounded_by_its_owner() -> None:
    """AWS's permissions-boundary rule: revoking the owner defangs the key."""
    graph = fakes(is_admin=False)
    owned = Caller(kind="service", subject="api-key:owned", nodes=UNBOUNDED, account_id=1)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            owned,
            status=BuildStatusFilter.DENIED,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_non_administrator_user_cannot_read_unreviewed_submissions() -> None:
    graph = fakes(is_admin=False)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            status=BuildStatusFilter.PENDING,
        )


@pytest.mark.asyncio
async def test_administrator_reads_the_pending_queue() -> None:
    graph = fakes(builds=[persisted_build(9)], is_admin=True)

    page = await list_builds(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        status=BuildStatusFilter.PENDING,
    )

    assert graph.build_queries.calls[-1].statuses == frozenset({Status.PENDING})
    assert [item.id for item in page.items] == [9]


@pytest.mark.asyncio
async def test_status_and_query_are_mutually_exclusive() -> None:
    graph = fakes()

    with pytest.raises(ValidationError):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            q="piston",
            status=BuildStatusFilter.CONFIRMED,
        )


@pytest.mark.asyncio
async def test_id_anchors_address_the_pages_on_either_side() -> None:
    builds = [persisted_build(9, Status.CONFIRMED), persisted_build(8, Status.CONFIRMED)]
    graph = fakes(builds=builds, is_admin=True)

    first = await list_builds(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        page_size=1,
    )

    assert [item.id for item in first.items] == [9]
    assert first.next == PageAnchor(after_id=9)
    assert first.prev is None

    back = await list_builds(
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
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            offset=20,
            after_id=9,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_id_anchors_are_refused_when_the_order_is_not_by_id() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="require ordering by id"):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            sort="-submission_time",
            after_id=9,
        )

    assert graph.build_queries.calls == []


@pytest.mark.asyncio
async def test_a_time_sorted_listing_pages_by_offset() -> None:
    graph = fakes(builds=[persisted_build(9, Status.CONFIRMED)], is_admin=True)

    page = await list_builds(
        graph.build_queries,
        cast(Any, None),
        graph.permissions,
        ACCOUNT,
        sort="submission_time",
        page_size=1,
        offset=20,
    )

    call = graph.build_queries.calls[-1]
    assert call.sort == BuildListSort(field="submission_time", descending=False)
    assert call.selector.offset == 20
    assert page.prev == PageAnchor(offset=19)


@pytest.mark.asyncio
async def test_an_unlisted_sort_field_is_refused() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="not supported"):
        await list_builds(
            graph.build_queries,
            cast(Any, None),
            graph.permissions,
            ACCOUNT,
            sort="title",
        )


@pytest.mark.asyncio
async def test_submitters_see_their_own_builds_in_every_status() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4, Status.DENIED)])

    page = await list_my_builds(graph.build_queries, ACCOUNT)

    call = graph.build_queries.calls[-1]
    assert call.statuses == frozenset(Status)
    assert call.submitter_account_id == 1
    assert [item.id for item in page.items] == [5, 4]


@pytest.mark.asyncio
async def test_an_account_submitter_can_list_builds_without_discord() -> None:
    graph = fakes(builds=[persisted_build(5)])
    minecraft_only = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)

    page = await list_my_builds(graph.build_queries, minecraft_only)

    assert graph.build_queries.calls[-1].submitter_account_id == 7
    assert [item.id for item in page.items] == [5]


@pytest.mark.asyncio
async def test_own_build_anchors_stay_scoped_to_the_submitter() -> None:
    """Anchors are plain identifiers a caller can forge, so the scoping lives in the query.

    Every listing ANDs its anchor into an authorization-scoped predicate, which is why dropping
    the signature that used to bind a cursor to one view leaks nothing: an anchor from someone
    else's page still only selects rows this caller may read.
    """
    graph = fakes(builds=[persisted_build(5)])
    other = Caller(kind="account", subject="account:2", nodes=UNBOUNDED, account_id=2)

    await list_my_builds(graph.build_queries, other, after_id=9)

    call = graph.build_queries.calls[-1]
    assert call.selector.after_id == 9
    assert call.submitter_account_id == 2


@pytest.mark.asyncio
async def test_own_builds_reject_a_service_credential() -> None:
    with pytest.raises(AuthenticationError):
        await list_my_builds(fakes().build_queries, SERVICE)
