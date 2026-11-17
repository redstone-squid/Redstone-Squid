"""Access forms keep the revision displayed before a modal or confirmation opens."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
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
from squid.permissions.models import PermissionRole
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


async def test_selected_role_assignment_does_not_resolve_a_new_role_with_the_same_slug() -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    screen = AccessScreen(
        admin,
        AsyncMock(spec=EditRunner),
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        capabilities=frozenset(),
        actor=AsyncMock(return_value=Actor(account_id=1)),
        authorize=AsyncMock(return_value=True),
    )
    saved = AsyncMock()
    screen._save = saved
    event = Submission()
    event.values = {"operation": "assign", "scope": "guild", "reason": "", "slug": "replacement"}
    await screen._change_assignment(
        cast(sl.SubmitEvent, event), base_revision=7, role=PermissionRole(id=15, slug="original", name="Original")
    )
    admin.role.assert_not_called()
    assert saved.call_args.args[1].role_id == 15
    assert saved.call_args.args[2] == 7


async def test_selected_rank_change_rechecks_permission() -> None:
    runner = AsyncMock(spec=EditRunner)
    screen = AccessScreen(
        AsyncMock(spec=PermissionAdministrationService),
        runner,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        capabilities=frozenset(),
        actor=AsyncMock(return_value=Actor(account_id=1)),
        authorize=AsyncMock(return_value=False),
    )
    await screen._rank_selected(15, 7, cast(sd.SubmitEvent, Submission()))
    runner.execute.assert_not_called()


@pytest.mark.parametrize("authorized", [True, False])
async def test_custom_role_pattern_uses_selected_identity_and_rechecks_permission(authorized: bool) -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    screen = AccessScreen(
        admin,
        AsyncMock(spec=EditRunner),
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        capabilities=frozenset(),
        actor=AsyncMock(return_value=Actor(account_id=1)),
        authorize=AsyncMock(return_value=authorized),
    )
    saved = AsyncMock()
    screen._save = saved
    event = Submission()
    event.values = {"operation": "exclude", "pattern": "build.*.view"}
    await screen._custom_role_pattern(15, 7, cast(sd.SubmitEvent, event))
    admin.role.assert_not_called()
    if authorized:
        saved.assert_awaited_once()
        assert saved.call_args.args[1].role_id == 15
        assert saved.call_args.args[1].pattern == "build.*.view"
        assert saved.call_args.args[2] == 7
    else:
        saved.assert_not_awaited()
        event.notice.assert_awaited_once()


@pytest.mark.parametrize(
    ("name", "expected"), [("Review Crew", "review-crew-2"), ("123", "role-123"), ("建築", "role-")]
)
async def test_role_creation_derives_available_slug(name: str, expected: str) -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.roles.return_value = (PermissionRole(id=15, slug="review-crew", name="Review Crew"),)
    admin.reserve_role_identity.return_value = 16
    screen = AccessScreen(
        admin,
        AsyncMock(spec=EditRunner),
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        capabilities=frozenset(),
        actor=AsyncMock(return_value=Actor(account_id=1)),
        authorize=AsyncMock(return_value=True),
    )
    saved = AsyncMock()
    screen._save = saved
    event = Submission()
    event.values = {"name": name, "slug": None, "rank": 0, "scope": "guild"}
    await screen._create_role(cast(sl.SubmitEvent, event), base_revision=7)
    assert saved.call_args.args[1].slug == expected
