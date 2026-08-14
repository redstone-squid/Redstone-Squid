"""Authoritative build collection views: moderation status and submitter ownership."""

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, NamedTuple, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.security import ANONYMOUS, UNBOUNDED, Principal
from squid.api.v1.builds import list_builds
from squid.api.v1.me import list_my_builds
from squid.api.v1.schemas.builds import BuildStatusFilter
from squid.builds.domain import Build, DoorBuild, Status
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.core.pagination import SignedCursor
from squid.runtime import ApiServices

SIGNER = SignedCursor(b"authoritative-build-view-test-secret")
# A realistic key: the scopes a service credential is actually issued, which do
# not include the moderation views.
SERVICE = Principal(
    kind="service",
    subject="api-key:test",
    nodes=frozenset({"build.submission.read", "build.submission.create"}),
)
ACCOUNT = Principal(kind="account", subject="account:1", nodes=UNBOUNDED, discord_id=123, account_id=1)


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
        submitter_id=123,
        submission_status=status,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        patterns=["Regular"],
        orientation="Door",
    )


def fakes(*, builds: list[Build] | None = None, is_admin: bool = False) -> Fakes:
    list_page = AsyncMock(return_value=builds or [])
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
        SIGNER,
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
            SIGNER,
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
            SIGNER,
            SERVICE,
            status=BuildStatusFilter.DENIED,
        )

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_key_carrying_the_node_is_still_bounded_by_its_owner() -> None:
    """AWS's permissions-boundary rule: revoking the owner defangs the key."""
    graph = fakes(is_admin=False)
    owned = Principal(kind="service", subject="api-key:owned", nodes=UNBOUNDED, account_id=1)

    with pytest.raises(AuthorizationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            SIGNER,
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
            SIGNER,
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
        SIGNER,
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
            SIGNER,
            ACCOUNT,
            q="piston",
            status=BuildStatusFilter.CONFIRMED,
        )


@pytest.mark.asyncio
async def test_status_cursors_do_not_carry_across_views() -> None:
    builds = [persisted_build(9, Status.CONFIRMED), persisted_build(8, Status.CONFIRMED)]
    graph = fakes(builds=builds, is_admin=True)

    page = await list_builds(
        graph.services.build_queries,
        cast(Any, None),
        graph.services.permissions,
        SIGNER,
        ACCOUNT,
        page_size=1,
    )

    assert page.has_more is True
    assert page.next_cursor is not None
    with pytest.raises(ValidationError):
        await list_builds(
            graph.services.build_queries,
            cast(Any, None),
            graph.services.permissions,
            SIGNER,
            ACCOUNT,
            status=BuildStatusFilter.PENDING,
            cursor=page.next_cursor,
        )


@pytest.mark.asyncio
async def test_submitters_see_their_own_builds_in_every_status() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4, Status.DENIED)])

    page = await list_my_builds(graph.services.build_queries, SIGNER, ACCOUNT)

    kwargs = awaited_kwargs(graph.list_page)
    assert kwargs["statuses"] == frozenset(Status)
    assert kwargs["submitter_account_id"] == 1
    assert [item.id for item in page.items] == [5, 4]


@pytest.mark.asyncio
async def test_provider_neutral_submitter_can_list_builds_without_discord() -> None:
    graph = fakes(builds=[persisted_build(5)])
    minecraft_only = Principal(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)

    page = await list_my_builds(graph.services.build_queries, SIGNER, minecraft_only)

    assert awaited_kwargs(graph.list_page)["submitter_account_id"] == 7
    assert [item.id for item in page.items] == [5]


@pytest.mark.asyncio
async def test_own_build_cursors_are_bound_to_the_submitter() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4)])
    other = Principal(kind="account", subject="account:2", nodes=UNBOUNDED, discord_id=456, account_id=2)

    page = await list_my_builds(graph.services.build_queries, SIGNER, ACCOUNT, page_size=1)

    assert page.next_cursor is not None
    with pytest.raises(ValidationError):
        await list_my_builds(graph.services.build_queries, SIGNER, other, cursor=page.next_cursor)


@pytest.mark.asyncio
async def test_own_builds_reject_a_service_credential() -> None:
    with pytest.raises(AuthenticationError):
        await list_my_builds(fakes().services.build_queries, SIGNER, SERVICE)
