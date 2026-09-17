"""Build mutation route tests."""

from typing import override

import pytest
from fastapi import Response
from pydantic import ValidationError

from squid.api.security import Caller, subject_for
from squid.api.v1.builds import edit_build
from squid.api.v1.schemas.builds import BuildPatch, DoorPatch
from squid.builds.application import BuildEditor, BuildService
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, DoorBuild, Status
from squid.builds.errors import BuildRevisionRequiredError
from squid.core.errors import AuthorizationError
from tests.unit.api.fakes import credential_nodes

ACCOUNT = Caller(
    kind="account",
    subject="account:1",
    nodes=credential_nodes("build.submission.create"),
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


CLI = Caller(
    kind="cli",
    subject="account:1",
    nodes=credential_nodes("build.submission.create"),
    account_id=1,
)


class BuildRecorder(BuildService):
    def __init__(self, *, result: Build | None = None, edit_error: Exception | None = None) -> None:
        self.result = result or persisted_build()
        self.edit_error = edit_error
        self.edits: list[tuple[BuildEditor, int, BuildEditPatch, int | None]] = []

    @override
    async def apply_edit(
        self,
        actor: BuildEditor,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
    ) -> Build:
        self.edits.append((actor, build_id, patch, expected_revision))
        if self.edit_error is not None:
            raise self.edit_error
        return self.result


@pytest.mark.asyncio
async def test_edit_hands_the_authorization_decision_to_the_service() -> None:
    """Who may edit is a build policy, not a transport one: the route validates
    the request, names the caller, and calls one method."""
    builds = BuildRecorder()

    http_response = Response()
    response = await edit_build(
        42,
        BuildPatch(extra_user_info="changed"),
        http_response,
        builds,
        ACCOUNT,
        '"build-42-r1"',
    )

    assert response.id == 42
    assert http_response.headers["etag"] == '"build-42-r1"'
    actor, build_id, patch, expected_revision = builds.edits[0]
    assert actor == BuildEditor(subject=subject_for(ACCOUNT))
    assert build_id == 42
    assert patch.extra_user_info == "changed"
    assert expected_revision == 1


@pytest.mark.asyncio
async def test_edit_surfaces_the_service_authorization_refusal() -> None:
    builds = BuildRecorder(edit_error=AuthorizationError())

    with pytest.raises(AuthorizationError):
        await edit_build(42, BuildPatch(), Response(), builds, ACCOUNT, '"build-42-r1"')


@pytest.mark.asyncio
async def test_edit_requires_an_if_match_revision() -> None:
    """Checked before the service is reached: a blind overwrite is a bad request,
    not an authorization question."""
    builds = BuildRecorder()

    with pytest.raises(BuildRevisionRequiredError):
        await edit_build(42, BuildPatch(), Response(), builds, ACCOUNT)

    assert builds.edits == []


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
