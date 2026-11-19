"""Canonical permission and internal-role administration workspace."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.edit_controller import EditController
from squid.bot.lookup import pick
from squid.bot.ui import tr
from squid.bot.utils.accounts import get_or_create_discord_account_id
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.permissions.application.administration import PermissionAdministrationService, effect_label
from squid.permissions.application.edits import (
    ASSIGNMENT_EDIT,
    GRANT_EDIT,
    INCLUDE_EDIT,
    PATTERN_EDIT,
    PERMISSION_GRAPH,
    RANK_EDIT,
    ROLE_CREATE,
    ROLE_DELETE,
    AssignmentValues,
    GrantValues,
    IncludeValues,
    PatternValues,
    PermissionValues,
    RankValues,
    RoleDeletionValues,
    RolePresenceValues,
)
from squid.permissions.domain import CATALOGUE, Decision, Effect, Reason
from squid.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid.permissions.domain.models import PatternMode
from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment
from squid.suggestions.domain import Suggestion
from squid_i18n import md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target

type ActorProvider = Callable[[], Awaitable[Actor]]

_DENIED = tr(t"access-change-forbidden = You are no longer allowed to change access controls.")


@dataclass(frozen=True)
class AccessChange:
    values: GrantValues | AssignmentValues
    revision: int
    subject: str
    member_id: int | None
    item: str
    operation: str


class AccessScreen(sd.Screen[None, Any, RedstoneSquid]):
    """Browse and administer one subject's access in a guild.

    Grants held at open determine which tasks are available. Mutation handlers
    recheck authorization and obtain a fresh administrative actor; the application
    service then enforces role rank, scope, and delegation limits.
    """

    policy = sd.ResponseSpec(nav=sd.navigation_controls.page_jump_nav, timeout=300, session=sd.SessionSpec("access"))
    _account_id: int | None = sl.state(None)
    _discord_role_id: int | None = sl.state(None)
    _subject_label: str = sl.state("")
    _scope_key: str = sl.state("guild")
    _selected_pattern: tuple[str, int] | None = sl.state(None)
    _selected_role: tuple[PermissionRole, int] | None = sl.state(None)
    _revision: int = sl.state(default=0)
    _review: AccessChange | None = sl.state(None)
    _role_tools: int | None = sl.state(None)
    _page: str = sl.state("home")
    _content: tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...] = sl.state(())
    _target_chosen: bool = sl.state(True)
    _member_id: int | None = sl.state(None)
    _action: str | None = sl.state(None)
    _pending_delete: tuple[str, int, int] | None = sl.state(default=None)
    _decision: sp.ComponentDriver[sp.DecisionState, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = (
        sl.state(default=None)
    )

    def __init__(
        self,
        admin: PermissionAdministrationService,
        runner: EditRunner,
        *,
        guild_id: int,
        account_id: int | None,
        discord_role_id: int | None,
        subject_label: str,
        member_id: int | None = None,
        target_chosen: bool = True,
        guild_name: str = "this server",
        bot: RedstoneSquid | None = None,
        grants: Grants,
        actor: ActorProvider,
    ) -> None:
        self._bot = bot
        self._target_chosen = target_chosen
        self._member_id = member_id
        self._guild_name = guild_name
        self._admin = admin
        self._guild_id = guild_id
        self._account_id = account_id
        self._discord_role_id = discord_role_id
        self._subject_label = subject_label
        self.grants = grants
        self.edits = EditController(runner, actor, self._refresh)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        self._revision = await self._admin.revision()
        page = self._page
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        tasks = (
            ("check", tr(t"access-check = Check access"), PERM_SUBJECT_INSPECT in self.grants),
            ("edit", tr(t"access-edit = Give or remove access"), self._may_grant or self._may_manage_roles),
            ("roles", tr(t"access-view-internal-roles = Manage bot roles"), self._may_manage_roles),
            ("audit", tr(t"access-history = Recent changes"), PERM_AUDIT_VIEW in self.grants),
            (
                "reference",
                tr(t"access-permission-list = Find a bot action"),
                PERM_NODE_VIEW in self.grants and PERM_SUBJECT_INSPECT not in self.grants,
            ),
        )
        if page == "home":
            nodes.extend(
                (
                    sl.heading(tr(t"access-home = What do you want to do?")),
                    sl.action_controls(
                        *(
                            sl.action_control(label, partial(self._open, key), key=key)
                            for key, label, allowed in tasks
                            if allowed
                        ),
                        key="access-tasks",
                    ),
                )
            )
        elif page in {"check", "edit"}:
            nodes.extend(self._target_controls())
            if not self._target_chosen:
                nodes.append(
                    sl.note(
                        tr(
                            t"access-ui-select-the-member-or-discord-role-whose-access-you-want-to-manage = Select the member or Discord role whose access you want to manage."
                        )
                    )
                )
            elif page == "check":
                nodes.append(
                    sl.action_controls(
                        sl.action_control(
                            tr(t"access-list-rules = View existing rules and bot roles"),
                            partial(self._open, "records"),
                            key="view-rules",
                        ),
                        key="record-controls",
                    )
                )
                nodes.append(
                    sl.action_controls(
                        sl.action_control(
                            tr(t"access-find = Find a bot action"), self._choose_permission, key="action"
                        ),
                        key="action-controls",
                    )
                )
                if self._action:
                    nodes.extend(await self._result())
                    if self._may_grant:
                        nodes.append(
                            sl.action_controls(
                                sl.action_control(
                                    tr(t"access-edit-result = Change this access"),
                                    partial(self._open, "edit"),
                                    key="edit-result",
                                ),
                                key="edit-result-controls",
                            )
                        )
            else:
                if self._action and self._bot and PERM_SUBJECT_INSPECT in self.grants:
                    nodes.extend(await self._result())
                nodes.extend(self._subject_forms())
                if (
                    (self._selected_pattern or self._selected_role)
                    and PERM_SUBJECT_INSPECT in self.grants
                    and await self.grants.allows(PERM_SUBJECT_INSPECT)
                ):
                    target = dict(
                        account_id=self._account_id, discord_role_id=self._discord_role_id, guild_id=self._guild_id
                    )
                    rules = await self._admin.rules_for(**target)
                    assignments = await self._admin.assignments_for(**target)
                    nodes.append(
                        self._subject_browser(
                            tuple(
                                r
                                for r in rules
                                if r.scope_guild_id == self._scope_id
                                and self._selected_pattern
                                and r.pattern == self._selected_pattern[0]
                            ),
                            tuple(
                                a
                                for a in assignments
                                if a.scope_guild_id == self._scope_id
                                and self._selected_role
                                and a.role_id == self._selected_role[0].id
                            ),
                        )
                    )
        elif page == "records":
            if await self.grants.allows(PERM_SUBJECT_INSPECT):
                target = dict(
                    account_id=self._account_id, discord_role_id=self._discord_role_id, guild_id=self._guild_id
                )
                nodes.extend(
                    (
                        sl.heading(self._subject_label),
                        self._subject_browser(
                            await self._admin.rules_for(**target), await self._admin.assignments_for(**target)
                        ),
                    )
                )
            else:
                nodes.append(sl.note(_DENIED))
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"access-ui-back = Back"), partial(self._open, "check"), key="back"),
                    key="records-back",
                )
            )
        elif page == "roles":
            nodes.append(
                sl.note(
                    tr(
                        t"access-role-guide = A bot role bundles actions. Assign it to a member or a Discord role to give them those permissions."
                    )
                )
            )
            nodes.extend(self._role_forms())
            nodes.append(self._role_browser(await self._admin.roles(guild_id=self._guild_id)))
        elif page in {"role-settings", "role-actions"}:
            roles = await self._admin.roles(guild_id=self._guild_id)
            role = next((r for r in roles if r.id == self._role_tools), None)
            if role is None:
                nodes.append(sl.note(tr(t"access-ui-this-role-has-been-deleted = This role has been deleted.")))
            elif page == "role-settings":
                nodes.append(sl.heading(role.name))
                nodes.extend(self._role_editor(role, roles))
            else:
                leaves = self._admin.resolved_leaves(role, roles)
                nodes.append(self._catalogue(leaves, role.name))
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"access-ui-back-to-bot-roles = Back to bot roles"), partial(self._open, "roles"), key="back"
                    ),
                    key="role-back",
                )
            )
        elif page == "reference":
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"access-find = Find a bot action"), self._choose_permission, key="find"),
                    key="find-controls",
                )
            )
            if self._action:
                nodes.append(sl.note(CATALOGUE[self._action].description))
        elif page == "audit":
            if await self.grants.allows(PERM_AUDIT_VIEW):
                nodes.append(self._audit(await self._admin.audit(guild_id=self._guild_id, limit=50)))
            else:
                nodes.append(sl.note(_DENIED))
        if page in {"edit", "check", "reference"} and (self._selected_role or self._selected_pattern):
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"access-ui-back = Back"), self._go_back, key="back-selection"),
                    key="back-controls",
                )
            )
        if page != "home":
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"access-home-button = Home"), partial(self._open, "home"), key="home"),
                    key="home-controls",
                )
            )
        self._content = tuple(nodes)

    async def _go_back(self, event: sd.PressEvent) -> None:
        await self._reset_selection()

    async def _open(self, page: str, event: sd.PressEvent) -> None:
        self._page = page
        self._role_tools = None
        self._selected_role = None
        self._selected_pattern = (self._action, self._revision) if page == "edit" and self._action else None
        if page == "home":
            self._action = None
            self._scope_key = "guild"
        await self._refresh()

    async def _result(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        assert self._action and self._bot
        if not await self.grants.allows(PERM_SUBJECT_INSPECT):
            return (sl.note(_DENIED),)
        guild = self._bot.get_guild(self._guild_id)
        if guild is None:
            return (sl.note(tr(t"access-ui-this-server-is-unavailable = This server is unavailable.")),)
        if self._discord_role_id is not None:
            if guild.get_role(self._discord_role_id) is None:
                return (
                    sl.note(tr(t"access-ui-that-discord-role-has-been-deleted = That Discord role has been deleted.")),
                )
            actor = Actor(guild_id=guild.id, discord_role_ids=frozenset({self._discord_role_id}))
        else:
            assert self._member_id is not None
            try:
                member = await guild.fetch_member(self._member_id)
            except discord.NotFound:
                return (sl.note(tr(t"access-ui-this-member-has-left-the-server = This member has left the server.")),)
            actor = await self._bot.actors.resolve(member, account_id=self._account_id)
        decision = (await self._bot.services.permissions.check(actor, self._action)).decisions[0]
        return (
            sl.heading(CATALOGUE[self._action].description),
            sl.paragraph(_explanation(decision, self._discord_role_id is not None)),
        )

    @property
    def _scope_id(self) -> int | None:
        return _scope(self._scope_key, self._guild_id)

    def _target_controls(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        subject = self._subject_label
        server = self._guild_name if self._page != "edit" or self._scope_id is not None else "all servers"
        return (
            sl.heading(
                md_tr(t"access-target = {subject} in {server}")
                if self._target_chosen
                else tr(t"access-who = Whose access?")
            ),
            sl.entities(
                key="access-target",
                entity_type=sl.entity.EntityType.MENTIONABLE,
                selection=sl.controlled((), self._change_subject),
                placeholder=tr(t"access-target-picker = Choose a member or Discord role"),
            ),
            *(
                (
                    sl.choices(
                        sl.choice(tr(t"access-view-this-server = This server"), key="guild"),
                        sl.choice(tr(t"access-all-servers = All servers"), key="global"),
                        key="access-scope",
                        selection=sl.controlled((self._scope_key,), self._change_scope),
                    ),
                )
                if self._target_chosen and self._page == "edit" and PERM_GRANT_GLOBAL in self.grants
                else ()
            ),
        )

    async def _change_subject(self, event: sl.EntityEvent[sd.Request[Any, RedstoneSquid]]) -> None:
        target = event.selected[0]
        guild = event.context.guild
        if guild is None or not isinstance(target.id, int):
            return
        if target.kind is sl.entity.EntityKind.ROLE:
            role = guild.get_role(target.id)
            if role is None:
                await event.notice(tr(t"access-role-unavailable = That role is no longer available."))
                return
            self._account_id, self._discord_role_id = None, role.id
            self._member_id = None
            self._subject_label = role.name
        else:
            account = await get_or_create_discord_account_id(event.context.client.services.accounts, target.id)
            self._account_id, self._discord_role_id = account, None
            self._member_id = target.id
            member = guild.get_member(target.id)
            self._subject_label = member.display_name if member else f"<@{target.id}>"
        choosing_for_role = not self._target_chosen and self._selected_role is not None
        self._target_chosen = True
        if choosing_for_role:
            await self._refresh()
        else:
            await self._reset_selection()

    async def _change_scope(self, event: sl.ChoiceEvent) -> None:
        self._scope_key = event.selected[0]
        await self._reset_selection()

    async def _reset_selection(self) -> None:
        self._review = None
        self._action = None
        self._selected_role = None
        self._selected_pattern = None
        self.edits.forget((PERMISSION_GRAPH.identity,))
        await self._refresh()

    @property
    def _may_grant(self) -> bool:
        return PERM_GRANT_GUILD in self.grants or PERM_GRANT_GLOBAL in self.grants

    @property
    def _may_manage_roles(self) -> bool:
        return ROLE_DEFINITION_MANAGE_GUILD in self.grants or ROLE_DEFINITION_MANAGE in self.grants

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        review = self._review
        if review is not None:
            verb = {
                "allow": tr(t"access-ui-allow-action = Allow action"),
                "deny": tr(t"access-ui-deny-action = Deny action"),
                "forbid": tr(t"access-ui-always-deny-action = Always deny action"),
                "revoke": tr(t"access-ui-remove-rule = Remove rule"),
                "assign": tr(t"access-ui-assign-role = Assign role"),
                "unassign": tr(t"access-ui-remove-role = Remove role"),
            }[review.operation]
            where = self._guild_name if review.values.scope_guild_id is not None else "All servers"
            return (
                sl.heading(tr(t"access-ui-review-access-change = Review access change")),
                sl.fields(
                    sl.field(tr(t"access-ui-change = Change"), verb),
                    sl.field(tr(t"access-ui-for = For"), review.subject),
                    sl.field(
                        tr(t"access-ui-action-or-bot-role = Action or bot role"),
                        CATALOGUE[review.item].description
                        if isinstance(review.values, GrantValues) and review.item in CATALOGUE
                        else review.item,
                    ),
                    sl.field(tr(t"access-ui-where = Where"), where),
                    sl.field(
                        tr(t"access-ui-reason = Reason"),
                        review.values.reason or tr(t"access-ui-none-given = None given"),
                    ),
                ),
                sl.note(
                    tr(
                        t"access-ui-removing-a-rule-leaves-any-access-from-other-rules-in-place = Removing a rule leaves any access from other rules in place."
                    )
                    if review.operation == "revoke"
                    else tr(
                        t"access-ui-bot-wide-rules-can-have-different-outcomes-in-each-server = Bot-wide rules can have different outcomes in each server."
                    )
                    if review.values.scope_guild_id is None
                    else tr(
                        t"access-ui-this-change-applies-in-the-selected-server = This change applies in the selected server."
                    )
                ),
                sl.action_controls(
                    sl.action_control(verb, partial(self._confirm_change, expected=review), key="confirm-change"),
                    sl.action_control(tr(t"access-ui-back = Back"), self._cancel_change, key="back"),
                    key="review-actions",
                ),
            )
        if self._pending_delete is not None and self._decision is not None:
            slug = self._pending_delete[0]
            return (
                sl.section(
                    sl.heading(tr(t"access-view-delete-internal-role = Delete internal role")),
                    sl.paragraph(
                        md_tr(
                            t"access-view-delete-and-its-assignments = Delete **{slug}** and its assignments?",
                        )
                    ),
                ),
                self._decision,
            )
        return (*self._content, *self.edits.controls())

    async def _cancel_change(self, event: sd.PressEvent) -> None:
        self._review = None

    async def _confirm_change(self, event: sd.PressEvent, *, expected: AccessChange | None = None) -> None:
        review = self._review
        if review is None or (expected is not None and review is not expected):
            return
        rule = isinstance(review.values, GrantValues)
        grants = (
            (PERM_GRANT_GUILD, PERM_GRANT_GLOBAL) if rule else (ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE)
        )
        if not await self.grants.confirm(event, *grants, denied=_DENIED):
            self._review = None
            return
        if self._bot:
            guild = self._bot.get_guild(self._guild_id)
            role_id = review.values.discord_role_id
            if guild is None or (role_id is not None and guild.get_role(role_id) is None):
                self._review = None
                await event.notice(
                    tr(
                        t"access-ui-the-server-or-discord-role-is-no-longer-available = The server or Discord role is no longer available."
                    )
                )
                return
            if review.member_id is not None:
                try:
                    await guild.fetch_member(review.member_id)
                except discord.NotFound:
                    self._review = None
                    await event.notice(
                        tr(t"access-ui-this-member-has-left-the-server = This member has left the server.")
                    )
                    return
        self._review = None
        label = f"{review.operation.capitalize()} · {review.item} · {review.subject}"
        await self._save(GRANT_EDIT if rule else ASSIGNMENT_EDIT, review.values, review.revision, label)
        await event.notice(label)

    def _subject_browser(
        self, rules: tuple[PermissionGrant, ...], assignments: tuple[PermissionRoleAssignment, ...]
    ) -> sp.Browser[PermissionGrant | PermissionRoleAssignment, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        return sp.Browser(
            sl.sources.list_source((*rules, *assignments)),
            key="subject-access",
            identity=lambda row: f"{type(row).__name__}:{row.id}",
            label=_subject_item_label,
            summary=_subject_item_summary,
            detail=_subject_item_detail,
            page_size=8,
            title=tr(t"access-view-rules-and-assignments = Direct rules and assigned bot roles"),
            empty=tr(
                t"access-subject-rules-empty = No matching rule or assignment at this scope. Other rules may still apply."
            ),
        )

    def _subject_forms(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if not (self._may_grant or self._may_manage_roles):
            return ()
        target = (self._account_id, self._discord_role_id, self._scope_id)
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.note(
                tr(
                    t"access-edit-guide = Choose a permission to change one action. A bot role groups permissions you can assign together."
                )
            ),
            sl.action_controls(
                self._may_grant
                and sl.action_control(
                    tr(t"access-choose-permission = Change one action"),
                    self._choose_permission,
                    key="choose-permission",
                ),
                self._may_manage_roles
                and sl.action_control(
                    tr(t"access-choose-role = Assign or remove a bot role"), self._choose_role, key="choose-role"
                ),
                self._may_grant
                and sl.form(
                    tr(t"access-custom-pattern = Advanced permission pattern"),
                    self._rule_form(),
                    key="custom-pattern",
                    on_submit=partial(
                        self._change_rule,
                        target=target,
                        subject=self._subject_label,
                        member_id=self._member_id,
                        base_revision=self._revision,
                    ),
                ),
                key="subject-forms",
            ),
        ]
        if self._selected_pattern is not None or self._selected_role is not None:
            nodes.clear()
        if self._may_grant and self._selected_pattern is not None:
            pattern, revision = self._selected_pattern
            nodes.extend(
                (
                    sl.note(CATALOGUE[pattern].description if pattern in CATALOGUE else pattern),
                    sl.form(
                        tr(t"access-view-change-direct-rule = Set or remove this permission"),
                        _without(_RuleForm().spec(), "pattern"),
                        key="rule",
                        on_submit=partial(
                            self._change_rule,
                            target=target,
                            subject=self._subject_label,
                            member_id=self._member_id,
                            base_revision=revision,
                            pattern=pattern,
                        ),
                    ),
                )
            )
        if self._may_manage_roles and self._selected_role is not None:
            role, revision = self._selected_role
            nodes.extend(
                (
                    sl.note(role.name),
                    sl.form(
                        tr(t"access-view-change-role-assignment = Assign or remove this role"),
                        _without(_AssignmentForm().spec(), "slug"),
                        key="assignment",
                        on_submit=partial(
                            self._change_assignment,
                            target=target,
                            subject=self._subject_label,
                            member_id=self._member_id,
                            base_revision=revision,
                            role=role,
                        ),
                    ),
                )
            )
        return tuple(nodes)

    async def _choose_permission(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        revision = self._revision

        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            self._action = item.value
            self._selected_role = None
            self._selected_pattern = (item.value, revision)
            await self._refresh()

        await pick(event, "permission_nodes", tr(t"access-find = Find a bot action"), selected)

    async def _choose_role(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        revision = self._revision

        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            role = next(
                (role for role in await self._admin.roles(guild_id=self._guild_id) if str(role.id) == item.value), None
            )
            if role is None:
                await _event.notice(tr(t"access-role-unavailable = That role is no longer available."))
                return
            self._selected_pattern = None
            self._action = None
            self._selected_role = (role, revision)
            await self._refresh()

        await pick(event, "permission_role_ids", tr(t"access-choose-role = Assign or remove a bot role"), selected)

    @staticmethod
    def _rule_form() -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        return _RuleForm().spec()

    def _role_browser(
        self,
        roles: tuple[PermissionRole, ...],
    ) -> sp.Browser[PermissionRole, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        return sp.Browser(
            sl.sources.list_source(roles),
            key="roles",
            identity=lambda role: str(role.id),
            label=lambda role: role.name,
            summary=_role_summary,
            detail=lambda role: self._role_editor(role, roles),
            page_size=8,
            title=tr(t"access-view-internal-roles = Manage bot roles"),
            empty=tr(t"access-role-list-empty = No internal roles are visible in this server."),
        )

    def _role_forms(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        return (
            sl.action_controls(
                sl.form(
                    tr(t"access-view-create-internal-role = Create a bot role"),
                    self._create_role_form(),
                    key="create-role",
                    on_submit=partial(self._create_role, base_revision=self._revision),
                ),
                key="role-forms",
            ),
        )

    def _role_editor(
        self, role: PermissionRole, roles: tuple[PermissionRole, ...]
    ) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        revision = self._revision
        if self._role_tools != role.id:
            leaves = self._admin.resolved_leaves(role, roles)
            return (
                sl.note(_role_summary(role)),
                sl.note(f"{len(leaves)} actions granted"),
                sl.action_controls(
                    sl.action_control(
                        tr(t"access-ui-view-granted-actions = View granted actions"),
                        partial(self._role_actions, role.id),
                        key="view-actions",
                    ),
                    sl.action_control(
                        tr(t"access-ui-assign-or-remove-this-role = Assign or remove this role"),
                        partial(self._assign_selected, role),
                        key="assign-selected",
                    ),
                    sl.action_control(
                        tr(t"access-ui-edit-role-settings = Edit role settings"),
                        partial(self._show_role_tools, role.id),
                        key="settings",
                    ),
                    key="role-options",
                ),
            )
        return (
            _role_detail(role, roles),
            sl.action_controls(
                sl.form(
                    tr(t"access-view-set-rank = Set rank"),
                    sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]](
                        role.name,
                        (
                            sl.forms.IntField(
                                key="rank",
                                label=tr(t"access-view-management-rank = Management rank"),
                                minimum=0,
                                maximum=999,
                                default=role.rank,
                            ),
                        ),
                    ),
                    key="rank",
                    on_submit=partial(self._rank_selected, role.id, revision),
                ),
                *(
                    sl.action_control(label, partial(self._pick_role_value, role, operation, revision), key=operation)
                    for operation, label in _ROLE_ACTIONS
                    if operation not in {"rank", "delete"}
                ),
                sl.action_control(
                    tr(t"access-view-delete-role = Delete role"),
                    partial(self._delete_selected, role, revision),
                    key="delete",
                    tone=sl.Tone.DANGER,
                ),
                key="role-actions",
            ),
            sl.form(
                tr(t"access-custom-pattern = Advanced permission pattern"),
                _RolePatternForm().spec(),
                key="custom-pattern",
                on_submit=partial(self._custom_role_pattern, role.id, revision),
            ),
        )

    async def _assign_selected(self, role: PermissionRole, event: sd.PressEvent) -> None:
        self._page = "edit"
        self._action = None
        self._selected_pattern = None
        self._selected_role = (role, self._revision)
        await self._refresh()

    async def _show_role_tools(self, role_id: int, event: sd.PressEvent) -> None:
        self._role_tools = role_id
        self._page = "role-settings"
        await self._refresh()

    async def _role_actions(self, role_id: int, event: sd.PressEvent) -> None:
        self._role_tools = role_id
        self._page = "role-actions"
        await self._refresh()

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _custom_role_pattern(self, role_id: int, revision: int, event: sd.SubmitEvent) -> None:
        form = _RolePatternForm.from_submission(event)
        await self._save(
            PATTERN_EDIT,
            PatternValues(
                role_id=role_id,
                pattern=form.pattern,
                mode={"include": PatternMode.INCLUDE, "exclude": PatternMode.EXCLUDE}.get(form.operation),
            ),
            revision,
            tr(t"access-view-internal-role-updated = Internal role updated."),
        )

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _rank_selected(self, role_id: int, revision: int, event: sd.SubmitEvent) -> None:
        await self._save(
            RANK_EDIT,
            RankValues(role_id=role_id, rank=int(str(event.values["rank"]))),
            revision,
            tr(t"access-view-internal-role-updated = Internal role updated."),
        )

    async def _pick_role_value(
        self, role: PermissionRole, operation: str, revision: int, event: sd.PressEvent[RedstoneSquid]
    ) -> None:
        composing = operation in {"compose", "decompose"}

        async def selected(source: sd.ActionEvent, item: Suggestion) -> None:
            if not await self.grants.confirm(
                source, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED
            ):
                return
            label = tr(t"access-view-internal-role-updated = Internal role updated.")
            if composing:
                included = next(
                    (role for role in await self._admin.roles(guild_id=self._guild_id) if str(role.id) == item.value),
                    None,
                )
                if included is None:
                    await source.notice(tr(t"access-role-unavailable = That role is no longer available."))
                    return
                await self._save(
                    INCLUDE_EDIT,
                    IncludeValues(role_id=role.id, included_id=included.id, present=operation == "compose"),
                    revision,
                    label,
                )
            else:
                await self._save(
                    PATTERN_EDIT,
                    PatternValues(
                        role_id=role.id,
                        pattern=item.value,
                        mode={"include": PatternMode.INCLUDE, "exclude": PatternMode.EXCLUDE}.get(operation),
                    ),
                    revision,
                    label,
                )

        await pick(
            event,
            "permission_role_ids" if composing else "permission_patterns",
            role.name,
            selected,
            confirm=dict(_ROLE_ACTIONS)[operation],
        )

    async def _delete_selected(self, role: PermissionRole, revision: int, _event: sd.PressEvent) -> None:
        self._request_delete(role, revision)

    def _request_delete(self, role: PermissionRole, revision: int) -> None:
        self._pending_delete = (role.slug, role.id, revision)
        self._decision = sp.Decision[ComponentsV2Target, sd.Request[Any, RedstoneSquid]](
            tr(t"access-role-delete-warning = Deleting an internal role removes every assignment of it."),
            (
                sp.DecisionOption("confirm", tr(t"access-view-delete-role = Delete role"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"access-cancel = Cancel")),
            ),
            key="delete-role",
        ).build_component(on_decide=self._decide_delete)

    def _create_role_form(self) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        form = _CreateRoleForm().spec()
        if ROLE_DEFINITION_MANAGE in self.grants and ROLE_DEFINITION_MANAGE_GUILD in self.grants:
            return form
        return _without(form, "scope")

    @staticmethod
    def _catalogue(
        names: tuple[str, ...], title: str
    ) -> sp.Browser[Any, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        nodes = tuple(CATALOGUE[name] for name in names)
        return sp.Browser(
            sl.sources.list_source(nodes),
            key="permission-catalogue",
            identity=lambda node: node.name,
            label=lambda node: node.description,
            summary=lambda node: node.name,
            detail=lambda node: sl.fields(
                sl.field(tr(t"access-view-scope = Where this applies"), node.scope.value),
                sl.field(tr(t"access-view-default = Default"), node.default.value),
                sl.field(tr(t"access-view-description = Description"), node.description),
            ),
            page_size=8,
            title=title,
            empty=tr(t"access-permission-catalogue-empty = This role grants no actions."),
        )

    @staticmethod
    def _audit(
        rows: tuple[PermissionAuditEntry, ...],
    ) -> sp.Browser[PermissionAuditEntry, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        return sp.Browser(
            sl.sources.list_source(rows),
            key="permission-audit",
            identity=lambda row: str(row.id),
            label=lambda row: row.action.replace("_", " ").capitalize(),
            summary=_audit_summary,
            detail=_audit_detail,
            page_size=8,
            title=tr(t"access-view-permission-audit = Recent changes"),
            empty=tr(t"access-audit-empty = No permission changes are recorded for this server."),
        )

    async def _save(self, kind: str, values: PermissionValues, revision: int | None, label: sl.TextLike) -> None:
        await self.edits.bind(kind, PERMISSION_GRAPH, self._revision if revision is None else revision).save(
            values.edit_values(), label
        )

    @requires_grant(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL, denied=_DENIED)
    async def _change_rule(
        self,
        event: sl.SubmitEvent,
        *,
        target: tuple[int | None, int | None, int | None] | None = None,
        subject: str | None = None,
        member_id: int | None = None,
        base_revision: int | None = None,
        pattern: str | None = None,
    ) -> None:
        account, discord_role, scope = target or (self._account_id, self._discord_role_id, self._scope_id)
        effect = {"allow": Effect.ALLOW, "deny": Effect.DENY, "forbid": Effect.FORBID}.get(
            str(event.values["operation"])
        )
        values = GrantValues(
            pattern=pattern or str(event.values["pattern"]),
            effect=effect,
            account_id=account,
            discord_role_id=discord_role,
            guild_id=self._guild_id,
            scope_guild_id=scope,
            reason=str(event.values.get("reason") or "") or None,
        )
        self._review = AccessChange(
            values,
            self._revision if base_revision is None else base_revision,
            subject or self._subject_label,
            member_id,
            pattern or str(event.values["pattern"]),
            str(event.values["operation"]),
        )

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _change_assignment(
        self,
        event: sl.SubmitEvent,
        *,
        target: tuple[int | None, int | None, int | None] | None = None,
        subject: str | None = None,
        member_id: int | None = None,
        base_revision: int | None = None,
        role: PermissionRole | None = None,
    ) -> None:
        account, discord_role, scope = target or (self._account_id, self._discord_role_id, self._scope_id)
        if role is None:
            role = await self._admin.role(str(event.values["slug"]), guild_id=self._guild_id)
        values = AssignmentValues(
            role_id=role.id,
            present=event.values["operation"] == "assign",
            account_id=account,
            discord_role_id=discord_role,
            guild_id=self._guild_id,
            scope_guild_id=scope,
            reason=str(event.values.get("reason") or "") or None,
        )
        self._review = AccessChange(
            values,
            self._revision if base_revision is None else base_revision,
            subject or self._subject_label,
            member_id,
            role.name,
            str(event.values["operation"]),
        )

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _create_role(self, event: sl.SubmitEvent, *, base_revision: int | None = None) -> None:
        form = _CreateRoleForm.from_submission(event)
        scope = str(event.values.get("scope") or ("global" if ROLE_DEFINITION_MANAGE in self.grants else "guild"))
        slug = form.slug
        if not slug:
            stem = re.sub(r"[^a-z0-9]+", "-", form.name.lower()).strip("-")[:28]
            if not stem or not stem[0].isalpha():
                stem = f"role-{stem}"[:28]
            if len(stem) < 2:
                stem += "-role"
            existing = {role.slug for role in await self._admin.roles(guild_id=_scope(scope, self._guild_id))}
            slug = stem
            suffix = 2
            while slug in existing:
                tail = f"-{suffix}"
                slug = stem[: 32 - len(tail)] + tail
                suffix += 1
        label = md_tr(t"access-view-created-internal-role = Created internal role **{slug}**.")
        await self._save(
            ROLE_CREATE,
            RolePresenceValues(
                role_id=await self._admin.reserve_role_identity(),
                slug=slug,
                name=form.name,
                rank=form.rank,
                guild_id=_scope(scope, self._guild_id),
            ),
            base_revision,
            label,
        )
        await event.notice(label)

    async def _decide_delete(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        pending = self._pending_delete
        self._pending_delete = None
        self._decision = None
        if pending is None or choice == "cancel":
            return
        if not await self.grants.confirm(
            event.source, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED
        ):
            return
        slug, identity, revision = pending
        label = md_tr(t"access-view-deleted-internal-role = Deleted internal role **{slug}**.")
        await self._save(ROLE_DELETE, RoleDeletionValues(role_id=identity), revision, label)
        await event.source.notice(label)


def _explanation(decision: Decision, role_only: bool) -> str:
    rule = decision.decisive_rule
    if role_only and rule is None:
        return "This Discord role contributes no rule for this action. Choose a member above to check their access."
    verdict = (
        {"allow": "Allows", "deny": "Denies", "forbid": "Always denies"}.get(rule.effect.name.lower(), "Denies")
        if role_only and rule
        else ("Yes" if decision.allowed else "No")
    )
    if rule is None:
        why = "Bot owners have access." if decision.reason is Reason.OWNER else "The default for this action applies."
    else:
        origin, _, identity = rule.source.partition(":")
        source = {
            "role": f"bot role {identity}",
            "account": "a direct member rule",
            "discord-role": f"Discord role <@&{identity}>",
            "discord": "Discord Manage Server",
        }.get(origin, rule.source)
        via = f" through Discord role <@&{rule.via.split(':')[-1]}>" if rule.via else ""
        where = "this server" if rule.scope_guild_id else "all servers"
        why = f"The deciding rule is {rule.pattern.raw} from {source}{via} ({where})."
    return f"{verdict}. {why}"


def _without(form: sl.forms.FormSpec[sd.Request[Any, Any]], *keys: str) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
    return replace(
        form,
        items=tuple(item for item in form.items if not isinstance(item, sl.forms.FormField) or item.key not in keys),
        prefill={key: value for key, value in form.prefill.items() if key not in keys},
    )


def _scope_field() -> sl.forms.ChoiceField[str]:
    return sl.forms.ChoiceField(
        key="scope",
        label=tr(t"access-view-scope = Where this applies"),
        default="guild",
        options=(
            sl.forms.ChoiceOption("guild", tr(t"access-view-this-server = This server"), "guild"),
            sl.forms.ChoiceOption("global", tr(t"access-view-everywhere = Everywhere"), "global"),
        ),
    )


def _scope(value: str, guild_id: int) -> int | None:
    return None if value == "global" else guild_id


def _subject_item_label(row: PermissionGrant | PermissionRoleAssignment) -> str:
    return row.pattern if isinstance(row, PermissionGrant) else row.role_slug


def _subject_item_summary(row: PermissionGrant | PermissionRoleAssignment) -> str:
    scope = "All servers" if row.scope_guild_id is None else "This server"
    effect = effect_label(row.effect) if isinstance(row, PermissionGrant) else "Assigned bot role"
    return f"{effect} · {scope}"


def _subject_item_detail(row: PermissionGrant | PermissionRoleAssignment) -> sl.semantic.Fields:
    if isinstance(row, PermissionGrant):
        return sl.fields(
            sl.field(tr(t"access-view-effect = Effect"), effect_label(row.effect)),
            sl.field(tr(t"access-view-pattern = Pattern"), row.pattern),
            sl.field(
                tr(t"access-view-scope = Where this applies"),
                "All servers" if row.scope_guild_id is None else "This server",
            ),
            sl.field(tr(t"access-view-reason = Reason"), row.reason or "—"),
        )
    return sl.fields(
        sl.field(tr(t"access-view-internal-role = Internal role"), row.role_slug),
        sl.field(
            tr(t"access-view-scope = Where this applies"),
            "All servers" if row.scope_guild_id is None else "This server",
        ),
    )


def _role_summary(role: PermissionRole) -> str:
    scope = "global" if role.guild_id is None else "this server"
    protected = " · built-in" if role.protected else ""
    return f"rank {role.rank} · {scope}{protected}"


def _role_detail(role: PermissionRole, roles: tuple[PermissionRole, ...]) -> sl.semantic.Fields:
    by_id = {item.id: item.slug for item in roles}
    return sl.fields(
        sl.field(tr(t"access-view-includes = Includes"), ", ".join(role.includes) or "—"),
        sl.field(tr(t"access-view-excludes = Excludes"), ", ".join(role.excludes) or "—"),
        sl.field(
            tr(t"access-view-composed-roles = Also grants these bot roles"),
            ", ".join(by_id[item] for item in role.includes_roles if item in by_id) or "—",
        ),
    )


def _audit_summary(row: PermissionAuditEntry) -> str:
    return f"{row.at} · {row.pattern or 'role change'}"


def _audit_detail(row: PermissionAuditEntry) -> sl.semantic.Fields:
    return sl.fields(
        sl.field(tr(t"access-view-action = Action"), row.action.replace("_", " ")),
        sl.field(tr(t"access-ui-subject = Subject"), f"{row.subject_kind} {row.subject_id}" if row.subject_id else "—"),
        sl.field(tr(t"access-ui-bot-role-id = Bot role ID"), str(row.role_id or "—")),
        sl.field(
            tr(t"access-ui-scope = Scope"), f"Server {row.scope_guild_id}" if row.scope_guild_id else "All servers"
        ),
        sl.field(tr(t"access-ui-effect = Effect"), effect_label(row.effect) if row.effect is not None else "—"),
        sl.field(tr(t"access-view-pattern = Pattern"), row.pattern or "—"),
        sl.field(tr(t"access-view-reason = Reason"), row.reason or "—"),
        sl.field(tr(t"access-view-actor-account = Actor account"), str(row.actor_account_id or "—")),
    )


_ROLE_ACTIONS = (
    ("include", tr(t"access-view-include-pattern = Include pattern")),
    ("exclude", tr(t"access-view-exclude-pattern = Exclude pattern")),
    ("remove-pattern", tr(t"access-view-remove-pattern = Remove pattern")),
    ("compose", tr(t"access-view-include-role = Include role")),
    ("decompose", tr(t"access-view-remove-included-role = Remove included role")),
    ("rank", tr(t"access-view-set-rank = Set rank")),
    ("delete", tr(t"access-view-delete-role = Delete role")),
)


class _RuleForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-view-change-direct-permission-rule = Change direct permission rule")

    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = What should change?"),
        options=(
            sl.forms.ChoiceOption("allow", tr(t"access-view-allow = Allow"), "allow"),
            sl.forms.ChoiceOption("deny", tr(t"access-view-deny = Deny"), "deny"),
            sl.forms.ChoiceOption(
                "forbid", tr(t"access-view-forbid = Always deny, even if another rule allows"), "forbid"
            ),
            sl.forms.ChoiceOption("revoke", tr(t"access-view-revoke = Remove this rule"), "revoke"),
        ),
    )
    pattern = sl.forms.TextField(label=tr(t"access-view-permission-pattern = Permission pattern"), maximum=200)
    reason = sl.forms.TextField(label=tr(t"access-view-reason = Reason"), required=False, maximum=500)


class _AssignmentForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-view-change-internal-role-assignment = Change internal role assignment")

    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = What should change?"),
        options=(
            sl.forms.ChoiceOption("assign", tr(t"access-view-assign = Assign"), "assign"),
            sl.forms.ChoiceOption("unassign", tr(t"access-view-unassign = Unassign"), "unassign"),
        ),
    )
    slug = sl.forms.TextField(label=tr(t"access-view-internal-role-slug = Internal role slug"), maximum=100)
    reason = sl.forms.TextField(label=tr(t"access-view-reason = Reason"), required=False, maximum=500)


class _CreateRoleForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-view-create-internal-role = Create a bot role")

    slug = sl.forms.TextField(
        label=tr(t"access-view-slug-override = Advanced: slug override"),
        description=tr(t"access-view-slug-help = Leave empty to generate a unique identifier from the display name."),
        required=False,
        maximum=32,
    )
    name = sl.forms.TextField(label=tr(t"access-view-display-name = Display name"), maximum=100)
    rank = sl.forms.IntField(
        label=tr(t"access-view-management-rank = Management rank"),
        description=tr(t"access-view-rank-help = Higher ranks can manage lower ranks. Choose a rank below your own."),
        default=0,
        minimum=0,
        maximum=999,
    )
    scope = _scope_field()


class _RolePatternForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-custom-pattern = Advanced permission pattern")

    pattern = sl.forms.TextField(
        label=tr(t"access-view-permission-pattern = Permission pattern"),
        description=tr(
            t"access-custom-pattern-help = For example, build.*.view matches view permissions across build resources."
        ),
        maximum=200,
    )
    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = What should change?"),
        options=tuple(sl.forms.ChoiceOption(key, label, key) for key, label in _ROLE_ACTIONS[:3]),
    )
