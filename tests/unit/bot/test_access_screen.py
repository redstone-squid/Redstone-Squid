"""Access forms keep the revision displayed before a modal or confirmation opens."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
from squid.bot.access_view import AccessScreen
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    EditValues,
    Revision,
    RevisionConflictError,
)
from squid.permissions.application.administration import PermissionAdministrationService
from squid.permissions.application.edits import PERMISSION_GRAPH, ROLE_DELETE
from squid.permissions.domain.catalogue import PERM_GRANT_GUILD
from squid_ui.runtime import transaction
from tests.support.edits import bound_revisions


class Submission:
    def __init__(self) -> None:
        self.values = {"operation": "allow", "pattern": "squid.perm.node.view", "scope": "guild", "reason": ""}
        self.errors: tuple[sl.forms.FormIssue, ...] = ()
        self.notice = AsyncMock()


async def test_access_modal_does_not_rebase_and_final_delete_discards_graph_history() -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.revision.return_value = 7
    runner = AsyncMock(spec=EditRunner)
    actor = Actor(account_id=1)

    async def execute(command: EditCommand, _actor: Actor, **_kwargs: object) -> EditResult:
        expected = bound_revisions(command)[0]
        if expected.value != admin.revision.return_value:
            raise RevisionConflictError(expected, admin.revision.return_value)
        revisions = (Revision(PERMISSION_GRAPH, expected.value + 1),)
        final = command.kind == ROLE_DELETE
        receipt = EditReceipt(command, EditValues.of({"effect": None}), command.values, revisions, final=final)
        admin.revision.return_value = revisions[0].value
        return EditResult(
            EditOutcome(command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=final),
            receipt,
        )

    runner.execute.side_effect = execute
    screen = AccessScreen(
        admin,
        runner,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        capabilities=frozenset({PERM_GRANT_GUILD}),
        actor=AsyncMock(return_value=actor),
        authorize=AsyncMock(return_value=True),
    )
    await screen.on_load()
    with transaction():
        await screen._change_rule(cast(sl.SubmitEvent, Submission()), base_revision=7)
    assert runner.execute.call_args.args[0].expected == (Revision(PERMISSION_GRAPH, 7),)
    assert screen._revision == 8
    assert len(screen.edits.undoable) == 1
    with transaction(), pytest.raises(RevisionConflictError):
        await screen._change_rule(cast(sl.SubmitEvent, Submission()), base_revision=7)
    # The modal sends the revision it displayed rather than rebasing onto the newer one.
    assert runner.execute.call_args.args[0].expected == (Revision(PERMISSION_GRAPH, 7),)
    assert len(screen.edits.undoable) == 1
    screen._pending_delete = ("role", 10, 8)
    event = AsyncMock()
    with transaction():
        await screen._decide_delete(event, "confirm")
    assert runner.execute.call_args.args[0].kind == ROLE_DELETE
    assert not screen.edits.undoable
