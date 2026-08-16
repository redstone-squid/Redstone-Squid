"""Build mutation route tests."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from pydantic import ValidationError

from squid.accounts.errors import ConsentRequiredError
from squid.api.security import Caller, subject_for
from squid.api.v1.builds import edit_build, submit_build
from squid.api.v1.schemas.builds import BuildPatch, DoorPatch, DoorSubmission
from squid.builds.application import BuildEditor
from squid.builds.domain import Build, DoorBuild, Status
from squid.builds.errors import BuildRevisionRequiredError, InvalidBuildError
from squid.core.errors import AuthorizationError
from squid.runtime import ApiServices
from tests.unit.api.fakes import credential_nodes

ACCOUNT = Caller(
    kind="account",
    subject="account:1",
    nodes=credential_nodes("build.submission.create"),
    discord_id=123,
    account_id=1,
)


def persisted_build(*, submitter_account_id: int = 1, status: Status = Status.PENDING) -> Build:
    return DoorBuild(
        id=42,
        submitter_account_id=submitter_account_id,
        submission_status=status,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        patterns=["Regular"],
        orientation="Door",
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
    assert submission.submitter_account_id == 1
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


@pytest.mark.asyncio
async def test_edit_hands_the_authorization_decision_to_the_service() -> None:
    """Who may edit is a build policy, not a transport one: the route validates
    the request, names the caller, and calls one method."""
    build = persisted_build()
    apply_edit = AsyncMock(return_value=build)
    services = cast(ApiServices, SimpleNamespace(builds=SimpleNamespace(apply_edit=apply_edit)))

    http_response = Response()
    response = await edit_build(
        42,
        BuildPatch(extra_user_info="changed"),
        http_response,
        services.builds,
        ACCOUNT,
        '"build-42-r1"',
    )

    assert response.id == 42
    assert http_response.headers["etag"] == '"build-42-r1"'
    call = apply_edit.await_args
    assert call is not None
    actor, build_id, patch = call.args
    assert actor == BuildEditor(subject=subject_for(ACCOUNT))
    assert build_id == 42
    assert patch.extra_user_info == "changed"
    assert call.kwargs == {"expected_revision": 1}


@pytest.mark.asyncio
async def test_edit_surfaces_the_service_authorization_refusal() -> None:
    apply_edit = AsyncMock(side_effect=AuthorizationError)
    services = cast(ApiServices, SimpleNamespace(builds=SimpleNamespace(apply_edit=apply_edit)))

    with pytest.raises(AuthorizationError):
        await edit_build(42, BuildPatch(), Response(), services.builds, ACCOUNT, '"build-42-r1"')


@pytest.mark.asyncio
async def test_edit_requires_an_if_match_revision() -> None:
    """Checked before the service is reached: a blind overwrite is a bad request,
    not an authorization question."""
    apply_edit = AsyncMock()
    builds = cast(Any, SimpleNamespace(apply_edit=apply_edit))

    with pytest.raises(BuildRevisionRequiredError):
        await edit_build(42, BuildPatch(), Response(), builds, ACCOUNT)

    apply_edit.assert_not_awaited()


def test_door_patch_flattens_onto_the_application_patch_names() -> None:
    """The nested wire object maps to the flat names BuildEditPatch speaks."""
    patch = BuildPatch(
        version_spec="1.21+",
        door=DoorPatch(door_dimensions=(2, 3, None), orientation="Trapdoor", patterns=["Full Lamp"]),
    )

    assert patch.edit_attributes() == {
        "version_spec": "1.21+",
        "door_dimensions": (2, 3, None),
        "door_orientation_type": "Trapdoor",
        "door_type": ["Full Lamp"],
    }


def test_patch_without_a_door_object_carries_no_door_fields() -> None:
    assert BuildPatch(extra_user_info="notes").edit_attributes() == {"extra_user_info": "notes"}


def test_door_patch_rejects_a_cleared_collection() -> None:
    with pytest.raises(ValidationError):
        DoorPatch(patterns=None)


def test_door_patch_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValidationError):
        DoorPatch(door_dimensions=(0, 2, None))
