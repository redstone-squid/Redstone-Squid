"""Access forms keep the revision displayed before a modal or confirmation opens."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.access_view import AccessScreen
from squid.bot.permissions_cog import ACCESS_NODES
from squid.core.actors import Actor
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    EditValues,
    Revision,
    RevisionConflictError,
)
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.application.edits import PERMISSION_GRAPH, ROLE_DELETE
from squid.core.permissions.domain import CATALOGUE, Decision, PermissionCheck, Reason
from squid.core.permissions.domain.catalogue import PERM_GRANT_GUILD, PERM_SUBJECT_INSPECT
from squid.core.permissions.models import PermissionRole
from squid_ui.ext.discord.testing import commit_render
from squid_ui.runtime import transaction
from squid_ui.testing import RecordingResponder, choice_event, submit_event
from tests.support.discord import make_layout_bot
from tests.support.edits import bound_revisions
from tests.support.grants import grants


def submission(**values: object) -> sl.SubmitEvent[Any]:
    defaults = {"operation": "allow", "pattern": "squid.perm.node.view", "scope": "guild", "reason": ""}
    return submit_event(values or defaults)


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
        grants=grants(PERM_GRANT_GUILD, current=True),
        actor=AsyncMock(return_value=actor),
    )
    await screen.on_load()
    with transaction():
        await screen._change_rule(submission(), base_revision=7)
        await screen._confirm_change(AsyncMock())
    assert runner.execute.call_args.args[0].expected == (Revision(PERMISSION_GRAPH, 7),)
    assert screen._revision == 8
    assert len(screen.edits.undoable) == 1
    with transaction():
        await screen._change_rule(submission(), base_revision=7)
    with transaction(), pytest.raises(RevisionConflictError):
        await screen._confirm_change(AsyncMock())
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
        grants=grants(current=True),
        actor=AsyncMock(return_value=Actor(account_id=1)),
    )
    saved = AsyncMock()
    screen._save = saved
    event = submission(operation="assign", scope="guild", reason="", slug="replacement")
    await screen._change_assignment(
        event, base_revision=7, role=PermissionRole(id=15, slug="original", name="Original")
    )
    saved.assert_not_awaited()
    await screen._confirm_change(AsyncMock())
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
        grants=grants(current=False),
        actor=AsyncMock(return_value=Actor(account_id=1)),
    )
    await screen._rank_selected(15, 7, submission())
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
        grants=grants(current=authorized),
        actor=AsyncMock(return_value=Actor(account_id=1)),
    )
    saved = AsyncMock()
    screen._save = saved
    event = submission(operation="exclude", pattern="build.*.view")
    await screen._custom_role_pattern(15, 7, event)
    admin.role.assert_not_called()
    if authorized:
        saved.assert_awaited_once()
        assert saved.call_args.args[1].role_id == 15
        assert saved.call_args.args[1].pattern == "build.*.view"
        assert saved.call_args.args[2] == 7
    else:
        saved.assert_not_awaited()
        assert len(cast(RecordingResponder, event.responder).notices) == 1


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
        grants=grants(current=True),
        actor=AsyncMock(return_value=Actor(account_id=1)),
    )
    saved = AsyncMock()
    screen._save = saved
    event = submission(name=name, slug=None, rank=0, scope="guild")
    await screen._create_role(event, base_revision=7)
    assert saved.call_args.args[1].slug == expected


async def test_rule_form_keeps_subject_and_scope_after_switching_targets() -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.revision.return_value = 7
    screen = AccessScreen(
        admin,
        AsyncMock(spec=EditRunner),
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(PERM_GRANT_GUILD),
        actor=AsyncMock(),
    )
    screen._save = saved = AsyncMock()
    await screen.on_load()
    controls = next(node for node in screen._subject_forms() if isinstance(node, sl.semantic.ActionControls))
    original = next(item for item in controls.items if isinstance(item, sl.semantic.FormTrigger))
    selection = MagicMock()
    selection.selected = (sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 99),)
    selection.context.guild.get_role.return_value.id = 99
    selection.context.guild.get_role.return_value.name = "Moderators"
    with transaction():
        await screen._change_subject(selection)
        await screen._change_scope(choice_event("global"))
        await original.on_submit(submission())
        saved.assert_not_awaited()
        assert screen._review
        assert screen._review.subject == "Member"
        await screen._confirm_change(AsyncMock())
    previous = saved.call_args.args[1]
    assert (previous.account_id, previous.discord_role_id, previous.scope_guild_id) == (2, None, 42)
    assert (screen._account_id, screen._discord_role_id, screen._scope_id) == (None, 99, None)


@pytest.mark.parametrize("page", ["home", "edit", "roles", "audit", "check"])
async def test_access_tasks_render_in_discord(page: str) -> None:
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.roles.return_value = ()
    admin.audit.return_value = ()
    admin.revision.return_value = 7
    screen = AccessScreen(
        admin,
        AsyncMock(spec=EditRunner),
        guild_id=42,
        subject_label="Alex",
        account_id=2,
        discord_role_id=None,
        grants=grants(*ACCESS_NODES),
        actor=AsyncMock(),
    )
    await screen.on_load()
    with transaction():
        await screen._open(page, AsyncMock())
    bot = make_layout_bot()
    payload = str(commit_render(bot.ui.mount(screen, access=sd.Owner(7), timeout=300)).to_components())
    assert {
        "home": "What do you want to do?",
        "check": "Find a bot action",
        "edit": "Change one action",
        "roles": "Create a bot role",
        "audit": "Recent changes",
    }[page] in payload
    assert ("Home" in payload) == (page != "home")


@pytest.mark.parametrize("role_id", [None, 99])
async def test_check_uses_selected_membership_or_only_the_selected_role(role_id: int | None) -> None:
    client = MagicMock()
    guild = client.get_guild.return_value
    guild.id = 42
    guild.fetch_member = AsyncMock()
    actor = Actor(account_id=2, discord_user_id=123, guild_id=42, discord_role_ids=frozenset({8, 99}))
    client.actors.resolve = AsyncMock(return_value=actor)
    node = next(iter(CATALOGUE))
    client.services.permissions.check = AsyncMock(
        return_value=PermissionCheck((Decision(node, allowed=False, reason=Reason.DEFAULT),))
    )
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.revision.return_value = 7
    screen = AccessScreen(
        admin,
        AsyncMock(),
        guild_id=42,
        guild_name="Redstone Lab",
        account_id=2 if role_id is None else None,
        member_id=None if role_id else 123,
        discord_role_id=role_id,
        subject_label="Helpers" if role_id else "Alex",
        grants=grants(PERM_SUBJECT_INSPECT),
        bot=client,
        actor=AsyncMock(),
    )
    with transaction():
        screen._action = node
        await screen._open("check", AsyncMock())
    rendered = str(commit_render(make_layout_bot().ui.mount(screen, access=sd.Owner(7), timeout=300)).to_components())
    assert "Redstone Lab" in rendered
    if role_id:
        client.services.permissions.check.assert_awaited_once_with(
            Actor(guild_id=42, discord_role_ids=frozenset({99})), node
        )
        assert "contributes no rule" in rendered
        guild.fetch_member.assert_not_awaited()
    else:
        guild.fetch_member.assert_awaited_once_with(123)
        client.services.permissions.check.assert_awaited_once_with(actor, node)
        assert "No. The default" in rendered


async def test_revoked_grant_cannot_confirm_review() -> None:
    runner = AsyncMock(spec=EditRunner)
    screen = AccessScreen(
        AsyncMock(),
        runner,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Alex",
        grants=grants(PERM_GRANT_GUILD),
        actor=AsyncMock(),
    )
    await screen._change_rule(submission(), base_revision=7)
    rendered = str(commit_render(make_layout_bot().ui.mount(screen, access=sd.Owner(7), timeout=300)).to_components())
    assert "Review access change" in rendered
    assert "Alex" in rendered
    screen.grants = grants(current=False)
    await screen._confirm_change(AsyncMock())
    runner.execute.assert_not_awaited()
