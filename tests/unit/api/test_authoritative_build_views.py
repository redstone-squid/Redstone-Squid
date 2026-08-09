"""Authoritative build collection views: moderation status and submitter ownership."""

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, NamedTuple, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.security import ANONYMOUS, Principal, Scope
from squid.api.v1.builds import list_builds
from squid.api.v1.me import list_my_builds
from squid.api.v1.schemas.builds import BuildStatusFilter
from squid.builds.domain import Build, BuildCategory, Status
from squid.core.errors import AuthenticationError, AuthorizationError, ValidationError
from squid.core.pagination import SignedCursor
from squid.runtime import ApiServices

SIGNER = SignedCursor(b"authoritative-build-view-test-secret")
SERVICE = Principal(kind="service", subject="api-key:test", scopes=frozenset(Scope))
USER = Principal(kind="user", subject="user:1", scopes=frozenset(Scope), discord_id=123, user_id=1)


class Fakes(NamedTuple):
    """A build-query service graph plus the mocks its routes are expected to drive."""

    services: ApiServices
    list_page: AsyncMock
    is_global_administrator: AsyncMock


def awaited_kwargs(mock: AsyncMock) -> Mapping[str, Any]:
    """Return the keyword arguments of a mock's single expected await."""
    assert mock.await_args is not None
    return mock.await_args.kwargs


def persisted_build(build_id: int, status: Status = Status.PENDING) -> Build:
    return Build(
        id=build_id,
        submitter_id=123,
        category=BuildCategory.DOOR,
        submission_status=status,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        door_type=["Regular"],
        door_orientation_type="Door",
    )


def fakes(*, builds: list[Build] | None = None, is_admin: bool = False) -> Fakes:
    list_page = AsyncMock(return_value=builds or [])
    is_global_administrator = AsyncMock(return_value=is_admin)
    services = SimpleNamespace(
        build_queries=SimpleNamespace(list_page=list_page),
        authorization=SimpleNamespace(is_global_administrator=is_global_administrator),
    )
    return Fakes(cast(ApiServices, services), list_page, is_global_administrator)


@pytest.mark.asyncio
async def test_anonymous_status_filter_defaults_to_confirmed_only() -> None:
    graph = fakes(builds=[persisted_build(1, Status.CONFIRMED)])

    await list_builds(graph.services, SIGNER, ANONYMOUS)

    assert awaited_kwargs(graph.list_page)["statuses"] == frozenset({Status.CONFIRMED})


@pytest.mark.asyncio
async def test_pending_view_requires_an_authenticated_human() -> None:
    graph = fakes()

    with pytest.raises(AuthenticationError):
        await list_builds(graph.services, SIGNER, ANONYMOUS, status=BuildStatusFilter.PENDING)

    graph.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_credentials_cannot_read_unreviewed_submissions() -> None:
    graph = fakes(is_admin=True)

    # An all-scopes key still fails: administrator status is bound to a Discord identity, which a
    # service credential never carries, so the grant is never even consulted.
    with pytest.raises(AuthorizationError):
        await list_builds(graph.services, SIGNER, SERVICE, status=BuildStatusFilter.DENIED)

    graph.is_global_administrator.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_administrator_user_cannot_read_unreviewed_submissions() -> None:
    graph = fakes(is_admin=False)

    with pytest.raises(AuthorizationError):
        await list_builds(graph.services, SIGNER, USER, status=BuildStatusFilter.PENDING)


@pytest.mark.asyncio
async def test_administrator_reads_the_pending_queue() -> None:
    graph = fakes(builds=[persisted_build(9)], is_admin=True)

    page = await list_builds(graph.services, SIGNER, USER, status=BuildStatusFilter.PENDING)

    assert awaited_kwargs(graph.list_page)["statuses"] == frozenset({Status.PENDING})
    assert [item.id for item in page.items] == [9]


@pytest.mark.asyncio
async def test_status_and_query_are_mutually_exclusive() -> None:
    graph = fakes()

    with pytest.raises(ValidationError):
        await list_builds(graph.services, SIGNER, USER, q="piston", status=BuildStatusFilter.CONFIRMED)


@pytest.mark.asyncio
async def test_status_cursors_do_not_carry_across_views() -> None:
    builds = [persisted_build(9, Status.CONFIRMED), persisted_build(8, Status.CONFIRMED)]
    graph = fakes(builds=builds, is_admin=True)

    page = await list_builds(graph.services, SIGNER, USER, page_size=1)

    assert page.has_more is True
    assert page.next_cursor is not None
    with pytest.raises(ValidationError):
        await list_builds(graph.services, SIGNER, USER, status=BuildStatusFilter.PENDING, cursor=page.next_cursor)


@pytest.mark.asyncio
async def test_submitters_see_their_own_builds_in_every_status() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4, Status.DENIED)])

    page = await list_my_builds(graph.services, SIGNER, USER)

    kwargs = awaited_kwargs(graph.list_page)
    assert kwargs["statuses"] == frozenset(Status)
    assert kwargs["submitter_id"] == 123
    assert [item.id for item in page.items] == [5, 4]


@pytest.mark.asyncio
async def test_own_build_cursors_are_bound_to_the_submitter() -> None:
    graph = fakes(builds=[persisted_build(5), persisted_build(4)])
    other = Principal(kind="user", subject="user:2", scopes=frozenset(Scope), discord_id=456, user_id=2)

    page = await list_my_builds(graph.services, SIGNER, USER, page_size=1)

    assert page.next_cursor is not None
    with pytest.raises(ValidationError):
        await list_my_builds(graph.services, SIGNER, other, cursor=page.next_cursor)


@pytest.mark.asyncio
async def test_own_builds_reject_a_service_credential() -> None:
    with pytest.raises(AuthenticationError):
        await list_my_builds(fakes().services, SIGNER, SERVICE)
