"""Build mutation route tests."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

from squid.accounts.errors import ConsentRequiredError
from squid.api.security import Principal
from squid.api.v1.builds import edit_build, submit_build
from squid.api.v1.schemas.builds import BuildPatch, DoorSubmission
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import BuildRevisionRequiredError, InvalidBuildError
from squid.core.errors import AuthorizationError
from squid.runtime import ApiServices

ACCOUNT = Principal(
    kind="account",
    subject="account:1",
    nodes=frozenset({"build.submission.create"}),
    discord_id=123,
    account_id=1,
)


def persisted_build(*, submitter_id: int = 123, status: Status = Status.PENDING) -> Build:
    return Build(
        id=42,
        submitter_id=submitter_id,
        category=BuildCategory.DOOR,
        submission_status=status,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        door_type=["Regular"],
        door_orientation_type="Door",
    )


@pytest.mark.asyncio
async def test_submit_maps_authenticated_identity_and_rejects_other_categories() -> None:
    submit_door = AsyncMock(return_value=persisted_build())
    services = cast(ApiServices, SimpleNamespace(builds=SimpleNamespace(submit_door=submit_door)))

    http_response = Response()
    response = await submit_build(DoorSubmission(door_size=(2, 2, None)), http_response, services.builds, ACCOUNT)

    assert response.id == 42
    assert submit_door.await_args is not None
    submission = submit_door.await_args.args[0]
    assert submission.submitter_id == 123
    assert not submission.ai_generated
    assert http_response.headers["etag"] == '"build-42-r1"'

    with pytest.raises(InvalidBuildError):
        await submit_build(
            DoorSubmission(category="extender", door_size=(2, 2, None)), Response(), services.builds, ACCOUNT
        )


@pytest.mark.asyncio
async def test_submit_gates_new_accounts_on_current_consent() -> None:
    services = cast(ApiServices, SimpleNamespace(builds=SimpleNamespace(submit_door=AsyncMock())))
    pending = replace(ACCOUNT, consent_pending=True)

    with pytest.raises(ConsentRequiredError) as error:
        await submit_build(DoorSubmission(door_size=(2, 2, None)), Response(), services.builds, pending)

    assert error.value.public_context == {"consent_url": "/v1/users/me/consent"}


class EditLease:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.commit = AsyncMock(return_value=build)

    async def __aenter__(self) -> "EditLease":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_edit_checks_ownership_while_lease_is_held() -> None:
    lease = EditLease(persisted_build(submitter_id=999))
    services = cast(
        ApiServices,
        SimpleNamespace(
            builds=SimpleNamespace(edit=lambda *_args, **_kwargs: lease),
            permissions=SimpleNamespace(allows=AsyncMock(return_value=False)),
        ),
    )

    with pytest.raises(AuthorizationError):
        await edit_build(
            42,
            BuildPatch(extra_user_info="changed"),
            Response(),
            services.builds,
            services.permissions,
            ACCOUNT,
            '"build-42-r1"',
        )

    lease.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_administrator_can_edit_confirmed_build() -> None:
    build = persisted_build(submitter_id=999, status=Status.CONFIRMED)
    lease = EditLease(build)
    services = cast(
        ApiServices,
        SimpleNamespace(
            builds=SimpleNamespace(edit=lambda *_args, **_kwargs: lease),
            permissions=SimpleNamespace(allows=AsyncMock(return_value=True)),
        ),
    )

    http_response = Response()
    response = await edit_build(
        42,
        BuildPatch(extra_user_info=None),
        http_response,
        services.builds,
        services.permissions,
        ACCOUNT,
        '"build-42-r1"',
    )

    assert response.id == 42
    assert http_response.headers["etag"] == '"build-42-r1"'
    lease.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_edit_requires_an_if_match_revision() -> None:
    builds = cast(Any, SimpleNamespace())

    with pytest.raises(BuildRevisionRequiredError):
        await edit_build(42, BuildPatch(), Response(), builds, cast(Any, None), ACCOUNT)
