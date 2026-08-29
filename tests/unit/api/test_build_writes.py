"""Build mutation route tests."""

from dataclasses import replace

import pytest
from fastapi import Response
from pydantic import ValidationError

from squid.accounts.errors import ConsentRequiredError
from squid.api.security import Caller, subject_for
from squid.api.v1.builds import edit_build, submit_build
from squid.api.v1.schemas.builds import BuildPatch, DoorPatch, DoorSubmission
from squid.builds.application import BuildEditor, BuildService
from squid.builds.application.commands import DoorSubmissionInput
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, DoorBuild, Status
from squid.builds.errors import BuildRevisionRequiredError, InvalidBuildError
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
        self.submissions: list[DoorSubmissionInput] = []
        self.edits: list[tuple[BuildEditor, int, BuildEditPatch, int | None]] = []

    async def submit_door(self, submission: DoorSubmissionInput) -> DoorBuild:
        self.submissions.append(submission)
        assert isinstance(self.result, DoorBuild)
        return self.result

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
async def test_a_cli_caller_with_no_discord_identity_can_submit() -> None:
    """Refused before: the gate demanded a snowflake the submission never used."""
    builds = BuildRecorder()

    response = await submit_build(DoorSubmission(door_size=(2, 2, None)), Response(), builds, CLI)

    assert response.id == 42
    assert builds.submissions[0].submitter_account_id == 1


@pytest.mark.asyncio
async def test_submit_maps_authenticated_identity_and_rejects_other_categories() -> None:
    builds = BuildRecorder()

    http_response = Response()
    response = await submit_build(DoorSubmission(door_size=(2, 2, None)), http_response, builds, ACCOUNT)

    assert response.id == 42
    submission = builds.submissions[0]
    assert submission.submitter_account_id == 1
    assert not submission.ai_generated
    assert http_response.headers["etag"] == '"build-42-r1"'

    with pytest.raises(InvalidBuildError):
        await submit_build(
            DoorSubmission(category="extender", door_size=(2, 2, None)), Response(), builds, ACCOUNT
        )


@pytest.mark.asyncio
async def test_submit_gates_new_accounts_on_current_consent() -> None:
    builds = BuildRecorder()
    pending = replace(ACCOUNT, consent_pending=True)

    with pytest.raises(ConsentRequiredError) as error:
        await submit_build(DoorSubmission(door_size=(2, 2, None)), Response(), builds, pending)

    assert error.value.public_context == {
        "consent_url": "/v1/users/me/consent",
        "notice_url": "/v1/consent/notice",
    }
    assert builds.submissions == []


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
