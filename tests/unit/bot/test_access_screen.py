"""Access forms keep the revision displayed before a modal or confirmation opens."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.access_view import AccessScreen
from squid.bot.permissions_cog import ACCESS_NODES
from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    EditValues,
    Revision,
    RevisionConflictError,
    require_revision,
)
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.application.edits import (
    ASSIGNMENT_EDIT,
    BULK_ASSIGNMENT_EDIT,
    GRANT_EDIT,
    PATTERN_EDIT,
    PERMISSION_GRAPH,
    ROLE_CREATE,
    ROLE_DELETE,
    AssignmentBatch,
    AssignmentValues,
    GrantValues,
    PatternValues,
    RolePresenceValues,
)
from squid.core.permissions.domain import CATALOGUE, Decision, PermissionCheck, Reason
from squid.core.permissions.domain.catalogue import PERM_GRANT_GLOBAL, PERM_GRANT_GUILD, PERM_SUBJECT_INSPECT
from squid.core.permissions.models import PermissionAuditEntry, PermissionRole, PermissionRoleAssignment
from squid_ui.ext.discord.testing import assert_within_limits, commit_render, delivered_to, message_harness, stage
from squid_ui.interactions import Actor as UIActor
from squid_ui.runtime import transaction
from squid_ui.testing import RecordingResponder, choice_event, submit_event
from tests.support.discord import make_layout_bot
from tests.support.edits import bound_revisions
from tests.support.grants import grants


def submission(**values: object) -> sl.SubmitEvent[Any]:
    defaults = {"operation": "allow", "pattern": "squid.perm.node.view", "scope": "guild", "reason": ""}
    return submit_event(values or defaults)


class PermissionEdits:
    def __init__(self, revision: int = 7) -> None:
        self.revision = revision
        self.commands: list[EditCommand] = []
        self.admin = AsyncMock(spec=PermissionAdministrationService)
        self.admin.revision.side_effect = lambda: self.revision
        self.admin.roles.return_value = ()
        self.admin.audit.return_value = ()

    async def execute(self, command: EditCommand, *, context: ActingContext) -> EditResult:
        actor = context.effective
        self.commands.append(command)
        (expected,) = bound_revisions(command)
        require_revision(expected, self.revision)
        self.revision += 1
        revisions = (Revision(PERMISSION_GRAPH, self.revision),)
        final = command.kind == ROLE_DELETE
        receipt = EditReceipt(command, EditValues.of({}), command.values, revisions, final=final)
        return EditResult(
            EditOutcome(command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=final),
            receipt,
        )


async def admin_actor() -> Actor:
    return Actor(account_id=1)


async def test_access_modal_does_not_rebase_and_final_delete_discards_graph_history() -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(PERM_GRANT_GUILD, current=True),
        actor=admin_actor,
    )
    await screen.on_load()
    with transaction():
        await screen._change_rule(submission(), target=screen._access_target(7))
        await screen._confirm_change(AsyncMock())
    assert edits.commands[-1].expected == (Revision(PERMISSION_GRAPH, 7),)
    assert len(screen.edits.undoable) == 1
    with transaction():
        await screen._change_rule(submission(), target=screen._access_target(7))
    with transaction(), pytest.raises(RevisionConflictError):
        await screen._confirm_change(AsyncMock())
    # The modal sends the revision it displayed rather than rebasing onto the newer one.
    assert edits.commands[-1].expected == (Revision(PERMISSION_GRAPH, 7),)
    assert len(screen.edits.undoable) == 1
    screen._pending_delete = ("role", 10, 8)
    event = AsyncMock()
    with transaction():
        await screen._decide_delete(event, "confirm")
    assert edits.commands[-1].kind == ROLE_DELETE
    assert not screen.edits.undoable


async def test_selected_role_assignment_does_not_resolve_a_new_role_with_the_same_slug() -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(*ACCESS_NODES),
        actor=admin_actor,
    )
    event = submission(operation="assign", scope="guild", reason="", slug="replacement")
    with transaction():
        await screen._change_assignment(
            event, target=screen._access_target(7), role=PermissionRole(id=15, slug="original", name="Original")
        )
        assert not edits.commands
    with transaction():
        await screen._confirm_change(sl.PressEvent(UIActor("1"), RecordingResponder(), context=cast(Any, None)))
    edits.admin.role.assert_not_called()
    (command,) = edits.commands
    assert command.kind == ASSIGNMENT_EDIT
    assert command.expected == (Revision(PERMISSION_GRAPH, 7),)
    assert AssignmentValues.model_validate_json(command.values.encoded).role_id == 15


async def test_selected_rank_change_rechecks_permission() -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(current=False),
        actor=admin_actor,
    )
    event = submission(rank=1)
    await screen._rank_selected(15, 7, event)
    assert not edits.commands
    assert len(cast(RecordingResponder, event.responder).notices) == 1


@pytest.mark.parametrize("authorized", [True, False])
async def test_custom_role_pattern_uses_selected_identity_and_rechecks_permission(authorized: bool) -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(*ACCESS_NODES, current=authorized),
        actor=admin_actor,
    )
    event = submission(operation="exclude", pattern="build.*.view")
    with transaction():
        await screen._custom_role_pattern(15, 7, event)
    edits.admin.role.assert_not_called()
    if authorized:
        (command,) = edits.commands
        assert command.kind == PATTERN_EDIT
        assert command.expected == (Revision(PERMISSION_GRAPH, 7),)
        values = PatternValues.model_validate_json(command.values.encoded)
        assert (values.role_id, values.pattern) == (15, "build.*.view")
    else:
        assert not edits.commands
        assert len(cast(RecordingResponder, event.responder).notices) == 1


@pytest.mark.parametrize(
    ("name", "expected"), [("Review Crew", "review-crew-2"), ("123", "role-123"), ("建築", "role-")]
)
async def test_role_creation_derives_available_slug(name: str, expected: str) -> None:
    edits = PermissionEdits()
    edits.admin.roles.return_value = (PermissionRole(id=15, slug="review-crew", name="Review Crew"),)
    edits.admin.reserve_role_identity.return_value = 16
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(*ACCESS_NODES),
        actor=admin_actor,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press("nav-roles")
        form = await ui.press_for_form("create-role")
        await form.submit({"name": name, "rank": 0, "scope": "guild"})
    (command,) = edits.commands
    assert command.kind == ROLE_CREATE
    assert RolePresenceValues.model_validate_json(command.values.encoded).slug == expected


async def test_rule_form_keeps_subject_and_scope_after_switching_targets() -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Member",
        grants=grants(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL),
        actor=admin_actor,
    )
    selection = sl.EntityEvent(
        UIActor("1"),
        RecordingResponder(),
        context=cast(
            Any, SimpleNamespace(guild=SimpleNamespace(get_role=lambda _: SimpleNamespace(id=99, name="Moderators")))
        ),
        selected=(sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 99),),
    )
    async with stage(screen, owner=object()) as ui:
        original = await ui.press_for_form("custom-pattern")
        with transaction():
            await screen._change_subject(selection)
            await screen._change_scope(choice_event("global"))
        await original.submit({"operation": "allow", "pattern": "squid.perm.node.view"})
        assert not edits.commands
        assert "## Review access change" in ui.texts()
        assert any("Member" in text for text in ui.texts())
        await ui.press("confirm-change")
        assert any("Moderators in all servers" in text for text in ui.texts())
    (command,) = edits.commands
    assert command.kind == GRANT_EDIT
    previous = GrantValues.model_validate_json(command.values.encoded)
    assert (previous.account_id, previous.discord_role_id, previous.scope_guild_id) == (2, None, 42)


@pytest.mark.parametrize("page", ["home", "edit", "roles", "audit"])
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
        "edit": "Change one action",
        "roles": "Create a bot role",
        "audit": "Audit log",
    }[page] in payload
    assert "Home" not in payload
    assert "Member or role access" in payload if page != "edit" else "Manage bot roles" in payload


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
        admin.assignments_for.return_value = ()
        admin.rules_for.return_value = ()
        screen._action = node
        await screen._open("edit", AsyncMock())
    rendered = str(commit_render(make_layout_bot().ui.mount(screen, access=sd.Owner(7), timeout=300)).to_components())
    assert "Redstone Lab" in rendered
    if role_id:
        client.services.permissions.check.assert_awaited_once_with(
            Actor(guild_id=42, discord_role_ids=frozenset({99})), *(n.name for n in CATALOGUE.values())
        )
        assert "contributes no rule" in rendered
        guild.fetch_member.assert_not_awaited()
    else:
        guild.fetch_member.assert_awaited_once_with(123)
        client.services.permissions.check.assert_awaited_once_with(actor, *(n.name for n in CATALOGUE.values()))
        assert "No. The default" in rendered


async def test_revoked_grant_cannot_confirm_review() -> None:
    edits = PermissionEdits()
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=2,
        discord_role_id=None,
        subject_label="Alex",
        grants=grants(PERM_GRANT_GUILD),
        actor=admin_actor,
    )
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form("custom-pattern")
        await form.submit({"operation": "allow", "pattern": "squid.perm.node.view"})
        assert "## Review access change" in ui.texts()
        assert any("Alex" in text for text in ui.texts())
        screen.grants = grants(PERM_GRANT_GUILD, current=False)
        await ui.press("confirm-change")
    assert not edits.commands


async def test_subject_overview_displays_inherited_privileges_next_to_recorded_assignments() -> None:
    client = MagicMock()
    guild = client.get_guild.return_value
    guild.fetch_member = AsyncMock()
    client.actors.resolve = AsyncMock(return_value=Actor(account_id=2, discord_user_id=123, guild_id=42))
    client.services.permissions.check = AsyncMock(
        return_value=PermissionCheck(tuple(Decision(node, allowed=True, reason=Reason.OWNER) for node in CATALOGUE))
    )
    admin = AsyncMock(spec=PermissionAdministrationService)
    admin.revision.return_value = 7
    admin.rules_for.return_value = ()
    admin.assignments_for.return_value = tuple(
        PermissionRoleAssignment(id=i, role_id=i, role_slug=f"reviewers-{i}", scope_guild_id=42) for i in range(1, 6)
    )
    screen = AccessScreen(
        admin,
        AsyncMock(),
        guild_id=42,
        guild_name="Lab",
        account_id=2,
        discord_role_id=None,
        member_id=123,
        subject_label="Alex",
        grants=grants(*ACCESS_NODES),
        bot=client,
        actor=AsyncMock(return_value=Actor(account_id=1)),
    )
    root = make_layout_bot().ui.mount(screen, access=sd.Owner(7), timeout=300)
    await root.send(delivered_to(message_harness()))
    view = commit_render(root)
    assert_within_limits(view)
    payload = str(view.to_components())
    for phrase in (
        "Effective privileges in Lab",
        "Bot owner",
        "reviewers-1",
        "Change member or Discord role",
        "Audit log",
    ):
        assert phrase in payload


async def test_audit_list_and_picker_identify_role_and_recipient() -> None:
    edits = PermissionEdits()
    admin = edits.admin
    accounts = SimpleNamespace(
        get_accounts=AsyncMock(
            return_value={
                1: Account(
                    id=1,
                    identities=(AccountIdentity.for_provider(IdentityProvider.DISCORD, "11", display_name="Alex"),),
                ),
                2: Account(
                    id=2,
                    identities=(AccountIdentity.for_provider(IdentityProvider.DISCORD, "22", display_name="Jamie"),),
                ),
            }
        )
    )
    admin.roles.return_value = (PermissionRole(id=15, slug="review", name="Review Crew"),)
    screen = AccessScreen(
        admin,
        edits,
        guild_id=42,
        account_id=None,
        discord_role_id=123,
        subject_label="Helpers",
        grants=grants(*ACCESS_NODES),
        actor=admin_actor,
        bot=cast(Any, SimpleNamespace(services=SimpleNamespace(accounts=accounts), get_guild=lambda _: None)),
    )
    admin.audit.return_value = (
        PermissionAuditEntry(
            id=3,
            action="role_assign",
            actor_account_id=1,
            subject_kind="account",
            subject_id=2,
            role_id=15,
            scope_guild_id=42,
        ),
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press("nav-audit")
        assert "Assigned Review Crew to Jamie · Alex" in "\n".join(ui.texts())
        assert ui.query('SelectOption[label="Assigned Review Crew to Jamie"]').exists()


async def test_multi_recipient_picker_saves_one_role_for_a_member_and_discord_role() -> None:
    role = PermissionRole(id=7, slug="reviewers", name="Reviewers")
    edits = PermissionEdits(revision=9)
    screen = AccessScreen(
        edits.admin,
        edits,
        guild_id=42,
        account_id=1,
        discord_role_id=None,
        subject_label="Alex",
        grants=grants(*ACCESS_NODES),
        actor=admin_actor,
    )
    await screen.on_load()
    refs = (sl.entity.EntityRef(sl.entity.EntityKind.USER, 123), sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 456))
    with transaction():
        await screen._assign_selected(role, AsyncMock(spec=sd.PressEvent))
        await screen._select_recipients(sl.EntityEvent(UIActor("1"), RecordingResponder(), context=None, selected=refs))
    view = commit_render(make_layout_bot().ui.mount(screen, access=sd.Owner(7), timeout=300))
    assert_within_limits(view)
    assert "Apply role assignment" in str(view.to_components())

    guild = SimpleNamespace(
        id=42, get_role=lambda _: SimpleNamespace(id=456), fetch_member=AsyncMock(return_value=SimpleNamespace(id=123))
    )
    accounts = SimpleNamespace(get_or_create_identity=AsyncMock(return_value=Account(id=77)))
    context = cast(
        Any, SimpleNamespace(guild=guild, client=SimpleNamespace(services=SimpleNamespace(accounts=accounts)))
    )
    event = replace(submission(operation="assign", scope="global"), context=context)
    with transaction():
        await screen._apply_role(role, 9, refs, event)
    (command,) = edits.commands
    assert command.kind == BULK_ASSIGNMENT_EDIT
    assert command.expected == (Revision(PERMISSION_GRAPH, 9),)
    batch = AssignmentBatch.model_validate_json(command.values.encoded)
    assert [(a.account_id, a.discord_role_id, a.scope_guild_id) for a in batch.assignments] == [
        (77, None, None),
        (None, 456, None),
    ]
