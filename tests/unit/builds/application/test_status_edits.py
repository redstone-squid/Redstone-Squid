"""Review decisions fence stale screens and authorize undo independently."""

from unittest.mock import AsyncMock

import pytest

from squid.builds.application.edits import BUILD_STATUS_EDIT, BuildEditUnitOfWork, BuildStatusEdit, build_target
from squid.builds.domain import Status
from squid.builds.models import DoorBuild
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.edits.domain import EditCommand, EditValues, Revision, RevisionConflictError
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_APPROVE, BUILD_SUBMISSION_REJECT


@pytest.mark.parametrize("status", [Status.CONFIRMED, Status.DENIED])
async def test_review_captures_status_and_checks_current_permission(status: Status) -> None:
    uow = AsyncMock(spec=BuildEditUnitOfWork)
    uow.builds = AsyncMock()
    uow.builds.get_for_update.return_value = DoorBuild(id=7, revision=3, submission_status=Status.PENDING)
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.return_value = None
    actor = Actor(account_id=1)
    command = EditCommand(
        BUILD_STATUS_EDIT, (Revision(build_target(7), 3),), EditValues.of({"submission_status": status})
    )
    handler = BuildStatusEdit(permissions)
    prepared = await handler.prepare(uow, actor, command)
    assert prepared.before.unpack() == {"submission_status": Status.PENDING}
    assert prepared.after.unpack() == {"submission_status": status}
    permission = BUILD_SUBMISSION_APPROVE if status is Status.CONFIRMED else BUILD_SUBMISSION_REJECT
    permissions.require.assert_awaited_once_with(actor, permission, uow=uow)
    permissions.require.side_effect = AuthorizationError
    with pytest.raises(AuthorizationError):
        await handler.prepare(uow, actor, command)


async def test_review_rejects_stale_revision_and_already_decided_build() -> None:
    uow = AsyncMock(spec=BuildEditUnitOfWork)
    uow.builds = AsyncMock()
    uow.builds.get_for_update.return_value = DoorBuild(id=7, revision=4, submission_status=Status.DENIED)
    handler = BuildStatusEdit(AsyncMock(spec=PermissionService))
    values = EditValues.of({"submission_status": Status.CONFIRMED})
    with pytest.raises(RevisionConflictError):
        await handler.prepare(
            uow, Actor(account_id=1), EditCommand(BUILD_STATUS_EDIT, (Revision(build_target(7), 3),), values)
        )
    with pytest.raises(InvalidStateError):
        await handler.prepare(
            uow, Actor(account_id=1), EditCommand(BUILD_STATUS_EDIT, (Revision(build_target(7), 4),), values)
        )


async def test_undo_requires_the_original_decision_permission() -> None:
    uow = AsyncMock(spec=BuildEditUnitOfWork)
    uow.builds = AsyncMock()
    uow.builds.get_for_update.return_value = DoorBuild(id=7, revision=4, submission_status=Status.DENIED)
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.side_effect = AuthorizationError
    actor = Actor(account_id=1)
    command = EditCommand(
        BUILD_STATUS_EDIT,
        (Revision(build_target(7), 4),),
        EditValues.of({"submission_status": Status.PENDING}),
        purpose="undo",
    )
    with pytest.raises(AuthorizationError):
        await BuildStatusEdit(permissions).prepare(uow, actor, command)
    permissions.require.assert_awaited_once_with(actor, BUILD_SUBMISSION_REJECT, uow=uow)
