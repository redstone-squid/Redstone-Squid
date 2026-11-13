"""Build mutation route tests."""

from types import SimpleNamespace
from typing import cast, override
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError

from squid.api.security import Caller
from squid.api.v1.builds import edit_build
from squid.api.v1.schemas.builds import BuildPatch, DoorPatch, ExtenderPatch
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.errors import AuthorizationError
from squid.runtime import ApiServices
from tests.support.build_edit_runner import BuildEditRecorder
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
        self.runner = BuildEditRecorder(result or persisted_build(), error=edit_error)

    @override
    async def get(self, build_id: int) -> Build:
        assert build_id == self.runner.build.id
        return self.runner.build

    def services(self) -> ApiServices:
        return cast(ApiServices, SimpleNamespace(builds=self, edits=self.runner))


@pytest.mark.asyncio
async def test_edit_hands_the_authorization_decision_to_the_service() -> None:
    """The route supplies the actor and revision; the build service decides whether editing is allowed."""
    builds = BuildRecorder()

    http_response = Response()
    response = await edit_build(
        42,
        BuildPatch(extra_user_info="changed"),
        http_response,
        builds.services(),
        ACCOUNT,
        uuid4(),
        '"build-42-r1"',
    )

    assert response.id == 42
    assert http_response.headers["etag"] == '"build-42-r2"'
    command = builds.runner.commands[0]
    assert builds.runner.actors[0] == ACCOUNT.to_actor()
    assert command.expected[0].target.key == "42"
    assert command.values.unpack() == {"extra_user_info": "changed"}
    assert command.expected[0].value == 1


@pytest.mark.asyncio
async def test_edit_surfaces_the_service_authorization_refusal() -> None:
    builds = BuildRecorder(edit_error=AuthorizationError())

    with pytest.raises(AuthorizationError):
        await edit_build(42, BuildPatch(), Response(), builds.services(), ACCOUNT, uuid4(), '"build-42-r1"')


@pytest.mark.asyncio
async def test_edit_without_if_match_uses_latest_state() -> None:
    builds = BuildRecorder()
    await edit_build(42, BuildPatch(extra_user_info="changed"), Response(), builds.services(), ACCOUNT, uuid4())
    assert builds.runner.commands[0].expected[0].value is None


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


def test_name_and_visible_timings_preserve_explicit_clears() -> None:
    patch = BuildPatch(display_name=None, door=DoorPatch(visible_opening_time=None, visible_closing_time=7))
    assert patch.edit_attributes() == {
        "display_name": None,
        "visible_opening_time": None,
        "visible_closing_time": 7,
    }
    with pytest.raises(ValidationError):
        DoorPatch.model_validate({"visible_opening_time": -1})


def test_extender_patch_preserves_omission_and_clear_semantics() -> None:
    assert BuildPatch(extender=ExtenderPatch(orientation="Up")).edit_attributes() == {"extender_orientation": "Up"}
    assert BuildPatch(extender=ExtenderPatch(extension_length=None)).edit_attributes() == {"extension_length": None}
    assert BuildPatch(extender=ExtenderPatch(extension_time=4, retraction_time=None)).edit_attributes() == {
        "extension_time": 4,
        "retraction_time": None,
    }
    with pytest.raises(ValidationError):
        BuildPatch(extender=None)
    with pytest.raises(ValidationError):
        ExtenderPatch.model_validate({"extension_length": -1})
    with pytest.raises(ValidationError):
        ExtenderPatch.model_validate({"retraction_time": -1})


def test_typed_extender_timings_travel_in_the_extender_object() -> None:
    patch = BuildPatch.from_patch(BuildEditPatch(extension_time=4, retraction_time=6))

    assert patch.extender == ExtenderPatch(extension_time=4, retraction_time=6)
