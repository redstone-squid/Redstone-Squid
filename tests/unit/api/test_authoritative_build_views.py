"""Authoritative build collection views: moderation status and submitter ownership."""

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, NamedTuple, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.pagination import PageAnchor
from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.api.v1.builds import list_builds
from squid.api.v1.me import list_my_builds
from squid.api.v1.schemas.builds import BuildStatusFilter
from squid.builds.application import DEFAULT_BUILD_LIST_SORT, BuildListSort
from squid.builds.domain import Build, DoorBuild, Status
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.core.pagination import FIRST_PAGE, Page, keyset_page
from squid.runtime import ApiServices
from tests.unit.api.fakes import credential_nodes

# A realistic key: the scopes a service credential is actually issued, which do
# not include the moderation views.
SERVICE = Caller(
    kind="service",
    subject="api-key:test",
    nodes=credential_nodes("build.submission.read", "build.submission.create"),
)
ACCOUNT = Caller(kind="account", subject="account:1", nodes=UNBOUNDED, discord_id=123, account_id=1)


class Fakes(NamedTuple):
    """A build-query service graph plus the mocks its routes are expected to drive."""

    services: ApiServices
    list_page: AsyncMock
    allows: AsyncMock


def awaited_kwargs(mock: AsyncMock) -> Mapping[str, Any]:
    """Return the keyword arguments of a mock's single expected await."""
    assert mock.await_args is not None
    return mock.await_args.kwargs


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
    """Stand in for BuildQueryService, assembling pages the way the real service does."""
    rows = builds or []

    async def list_one_page(**kwargs: Any) -> Page[Build]:
        sort = kwargs.get("sort", DEFAULT_BUILD_LIST_SORT)
        return keyset_page(
            rows,
            selector=kwargs.get("selector", FIRST_PAGE),
            page_size=kwargs.get("page_size", 20),
            total=len(rows),
            keyset=sort.field == "id",
            id_of=lambda build: build.id or 0,
        )

    list_page = AsyncMock(side_effect=list_one_page)
    allows = AsyncMock(return_value=is_admin)
    services = SimpleNamespace(
        build_queries=SimpleNamespace(list_page=list_page),
        permissions=SimpleNamespace(allows=allows),
    )
    return Fakes(cast(ApiServices, services), list_page, allows)


@pytest.mark.asyncio
async def test_anonymous_status_filter_defaults_to_confirmed_only() -> None:
    graph = fakes(builds=[persisted_build(1, Status.CONFIRMED)])

    await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        ANONYMOUS,
    )

    assert awaited_kwargs(graph.list_page)["statuses"] == frozenset({Status.CONFIRMED})


@pytest.mark.asyncio
async def test_pending_view_requires_an_authenticated_human() -> None:
    graph = fakes()

    with pytest.raises(AuthenticationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ANONYMOUS,
            status=BuildStatusFilter.PENDING,
        )

    graph.list_page.assert_not_awaited()


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
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            SERVICE,
            status=BuildStatusFilter.DENIED,
        )

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_key_carrying_the_node_is_still_bounded_by_its_owner() -> None:
    """AWS's permissions-boundary rule: revoking the owner defangs the key."""
    graph = fakes(is_admin=False)
    owned = Caller(kind="service", subject="api-key:owned", nodes=UNBOUNDED, account_id=1)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            owned,
            status=BuildStatusFilter.DENIED,
        )

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_administrator_user_cannot_read_unreviewed_submissions() -> None:
    graph = fakes(is_admin=False)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ACCOUNT,
            status=BuildStatusFilter.PENDING,
        )


@pytest.mark.asyncio
async def test_administrator_reads_the_pending_queue() -> None:
    graph = fakes(builds=[persisted_build(9)], is_admin=True)

    page = await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        ACCOUNT,
        status=BuildStatusFilter.PENDING,
    )

    assert awaited_kwargs(graph.list_page)["statuses"] == frozenset({Status.PENDING})
    assert [item.id for item in page.items] == [9]


@pytest.mark.asyncio
async def test_status_and_query_are_mutually_exclusive() -> None:
    graph = fakes()

    with pytest.raises(ValidationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ACCOUNT,
            q="piston",
            status=BuildStatusFilter.CONFIRMED,
        )


@pytest.mark.asyncio
async def test_id_anchors_address_the_pages_on_either_side() -> None:
    builds = [persisted_build(9, Status.CONFIRMED), persisted_build(8, Status.CONFIRMED)]
    graph = fakes(builds=builds, is_admin=True)

    first = await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        ACCOUNT,
        page_size=1,
    )

    assert [item.id for item in first.items] == [9]
    assert first.next == PageAnchor(after_id=9)
    assert first.prev is None

    back = await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        ACCOUNT,
        page_size=1,
        before_id=8,
    )

    assert awaited_kwargs(graph.list_page)["selector"].before_id == 8
    assert back.prev is not None
    assert back.next == PageAnchor(after_id=8)


@pytest.mark.asyncio
async def test_pagination_parameters_are_mutually_exclusive() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="cannot be combined"):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ACCOUNT,
            offset=20,
            after_id=9,
        )

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_id_anchors_are_refused_when_the_order_is_not_by_id() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="require ordering by id"):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ACCOUNT,
            sort="-submission_time",
            after_id=9,
        )

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_time_sorted_listing_pages_by_offset() -> None:
    graph = fakes(builds=[persisted_build(9, Status.CONFIRMED)], is_admin=True)

    page = await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        ACCOUNT,
        sort="submission_time",
        page_size=1,
        offset=20,
    )

    kwargs = awaited_kwargs(graph.list_page)
    assert kwargs["sort"] == BuildListSort(field="submission_time", descending=False)
    assert kwargs["selector"].offset == 20
    assert page.prev == PageAnchor(offset=19)


@pytest.mark.asyncio
async def test_an_unlisted_sort_field_is_refused() -> None:
    graph = fakes(is_admin=True)

    with pytest.raises(ValidationError, match="not supported"):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            ACCOUNT,
            sort="title",
        )


@pytest.mark.asyncio
async def test_submitters_see_their_own_builds_in_every_status() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4, Status.DENIED)])

    page = await list_my_builds(graph.services.build_queries, ACCOUNT)

    kwargs = awaited_kwargs(graph.list_page)
    assert kwargs["statuses"] == frozenset(Status)
    assert kwargs["submitter_account_id"] == 1
    assert [item.id for item in page.items] == [5, 4]


@pytest.mark.asyncio
async def test_an_account_submitter_can_list_builds_without_discord() -> None:
    graph = fakes(builds=[persisted_build(5)])
    minecraft_only = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)

    page = await list_my_builds(graph.services.build_queries, minecraft_only)

    assert awaited_kwargs(graph.list_page)["submitter_account_id"] == 7
    assert [item.id for item in page.items] == [5]


@pytest.mark.asyncio
async def test_own_build_anchors_stay_scoped_to_the_submitter() -> None:
    """Anchors are plain identifiers a caller can forge, so the scoping lives in the query.

    Every listing ANDs its anchor into an authorization-scoped predicate, which is why dropping
    the signature that used to bind a cursor to one view leaks nothing: an anchor from someone
    else's page still only selects rows this caller may read.
    """
    graph = fakes(builds=[persisted_build(5)])
    other = Caller(kind="account", subject="account:2", nodes=UNBOUNDED, discord_id=456, account_id=2)

    await list_my_builds(graph.services.build_queries, other, after_id=9)

    kwargs = awaited_kwargs(graph.list_page)
    assert kwargs["selector"].after_id == 9
    assert kwargs["submitter_account_id"] == 2


@pytest.mark.asyncio
async def test_own_builds_reject_a_service_credential() -> None:
    with pytest.raises(AuthenticationError):
        await list_my_builds(fakes().services.build_queries, SERVICE)
