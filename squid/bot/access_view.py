"""Canonical permission and internal-role administration workspace."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from typing import Any, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.edit_controller import EditController
from squid.bot.lookup import pick
from squid.bot.ui import tr
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.permissions.application.administration import PermissionAdministrationService, effect_label, scope_label
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
from squid.permissions.domain import CATALOGUE, Effect, PermissionNode
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
type AccessAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class AccessScreen(sd.Screen[None, Any, RedstoneSquid]):
    """Browse and administer one subject's access in a guild.

    Initial capabilities determine which tabs are displayed. Mutation handlers
    recheck authorization and obtain a fresh administrative actor; the application
    service then enforces role rank, scope, and delegation limits.
    """

    policy = sd.ResponseSpec(nav=sd.navigation_controls.page_jump_nav, timeout=300, session=sd.SessionSpec("access"))
    _selected_pattern: tuple[str, int] | None = sl.state(None)
    _selected_role: tuple[PermissionRole, int] | None = sl.state(None)
    _revision: int = sl.state(default=0)
    _tabs: sp.ComponentDriver[sp.TabsState, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        default=None
    )
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
        capabilities: frozenset[PermissionNode],
        actor: ActorProvider,
        authorize: AccessAuthorizer,
    ) -> None:
        self._admin = admin
        self._guild_id = guild_id
        self._account_id = account_id
        self._discord_role_id = discord_role_id
        self._subject_label = subject_label
        self._capabilities = capabilities
        self._authorize = authorize
        self.edits = EditController(runner, actor, self._refresh)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        self._revision = await self._admin.revision()
        tabs: list[sp.Tab[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        if PERM_SUBJECT_INSPECT in self._capabilities:
            rules = await self._admin.rules_for(
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
            )
            assignments = await self._admin.assignments_for(
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
            )
            tabs.append(
                sp.Tab("subject", tr(t"access-view-subject = Subject"), self._subject_browser(rules, assignments))
            )
        subject_forms = self._subject_forms()
        if subject_forms:
            tabs.append(
                sp.Tab(
                    "assignments",
                    tr(t"access-view-rules-and-assignments = Rules and assignments"),
                    subject_forms,
                )
            )
        if self._may_manage_roles:
            roles = await self._admin.roles(guild_id=self._guild_id)
            tabs.append(sp.Tab("roles", tr(t"access-view-internal-roles = Internal roles"), self._role_browser(roles)))
            tabs.append(sp.Tab("role-editor", tr(t"access-view-edit-roles = Edit roles"), self._role_forms()))
        if PERM_NODE_VIEW in self._capabilities:
            tabs.append(sp.Tab("catalogue", tr(t"access-view-catalogue = Catalogue"), self._catalogue()))
        if PERM_AUDIT_VIEW in self._capabilities:
            audit = await self._admin.audit(guild_id=self._guild_id, limit=50)
            tabs.append(sp.Tab("audit", tr(t"access-view-audit = Audit"), self._audit(audit)))
        subject_label = self._subject_label
        self._tabs = sp.Tabs(
            tabs,
            key="access-tabs",
            title=md_tr(t"access-view-access-for = Access for {subject_label}"),
            initial_state=None if self._tabs is None else self._tabs.machine_state,
        )

    @property
    def _may_grant(self) -> bool:
        return PERM_GRANT_GUILD in self._capabilities or PERM_GRANT_GLOBAL in self._capabilities

    @property
    def _may_manage_roles(self) -> bool:
        return ROLE_DEFINITION_MANAGE_GUILD in self._capabilities or ROLE_DEFINITION_MANAGE in self._capabilities

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
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
        if self._tabs is None:
            return (sl.status(tr(t"access-view-loading-access-controls = Loading access controls.")),)
        return (self._tabs, *self.edits.refresh_controls())

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
            page_size=15,
            title=tr(t"access-view-rules-and-assignments = Rules and assignments"),
            empty=tr(t"access-subject-rules-empty = This subject has no explicit access rules or internal roles."),
        )

    def _subject_forms(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if not (self._may_grant or self._may_manage_roles):
            return ()
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.action_controls(
                self._may_grant
                and sl.action_control(
                    tr(t"access-choose-permission = Choose permission"),
                    self._choose_permission,
                    key="choose-permission",
                ),
                self._may_manage_roles
                and sl.action_control(
                    tr(t"access-choose-role = Choose internal role"), self._choose_role, key="choose-role"
                ),
                self._may_grant
                and sl.form(
                    tr(t"access-custom-pattern = Custom permission pattern"),
                    self._rule_form(),
                    key="custom-pattern",
                    on_submit=partial(self._change_rule, base_revision=self._revision),
                ),
                key="subject-forms",
            )
        ]
        if self._may_grant and self._selected_pattern is not None:
            pattern, revision = self._selected_pattern
            nodes.extend(
                (
                    sl.note(pattern),
                    sl.form(
                        tr(t"access-view-change-direct-rule = Change direct rule"),
                        _without(_RuleForm().spec(), "pattern"),
                        key="rule",
                        on_submit=partial(self._change_rule, base_revision=revision, pattern=pattern),
                    ),
                )
            )
        if self._may_manage_roles and self._selected_role is not None:
            role, revision = self._selected_role
            nodes.extend(
                (
                    sl.note(role.name),
                    sl.form(
                        tr(t"access-view-change-role-assignment = Change role assignment"),
                        _without(_AssignmentForm().spec(), "slug"),
                        key="assignment",
                        on_submit=partial(self._change_assignment, base_revision=revision, role=role),
                    ),
                )
            )
        return tuple(nodes)

    async def _choose_permission(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        revision = self._revision

        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            self._selected_pattern = (item.value, revision)
            await self._refresh()

        await pick(event, "permission_patterns", tr(t"access-choose-permission = Choose permission"), selected)

    async def _choose_role(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        revision = self._revision

        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            role = next(
                (role for role in await self._admin.roles(guild_id=self._guild_id) if str(role.id) == item.value), None
            )
            if role is None:
                await _event.notice(tr(t"access-role-unavailable = That role is no longer available."))
                return
            self._selected_role = (role, revision)
            await self._refresh()

        await pick(event, "permission_role_ids", tr(t"access-choose-role = Choose internal role"), selected)

    @staticmethod
    def _rule_form() -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        return _RuleForm().spec()

    @staticmethod
    def _assignment_form() -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        return _AssignmentForm().spec()

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
            page_size=15,
            title=tr(t"access-view-internal-roles = Internal roles"),
            empty=tr(t"access-role-list-empty = No internal roles are visible in this server."),
        )

    def _role_forms(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        return (
            sl.action_controls(
                sl.form(
                    tr(t"access-view-create-internal-role = Create internal role"),
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
                tr(t"access-custom-pattern = Custom permission pattern"),
                _RolePatternForm().spec(),
                key="custom-pattern",
                on_submit=partial(self._custom_role_pattern, role.id, revision),
            ),
        )

    async def _custom_role_pattern(self, role_id: int, revision: int, event: sd.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
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

    async def _rank_selected(self, role_id: int, revision: int, event: sd.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
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
            if not await self._authorize_mutation(source, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
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

    @staticmethod
    def _create_role_form() -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        return _CreateRoleForm().spec()

    @staticmethod
    def _catalogue() -> sp.Browser[Any, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        nodes = tuple(CATALOGUE.values())
        return sp.Browser(
            sl.sources.list_source(nodes),
            key="permission-catalogue",
            identity=lambda node: node.name,
            label=lambda node: node.name,
            summary=lambda node: f"{node.scope.value} · {node.default.value}",
            detail=lambda node: sl.fields(
                sl.field(tr(t"access-view-scope = Scope"), node.scope.value),
                sl.field(tr(t"access-view-default = Default"), node.default.value),
                sl.field(tr(t"access-view-description = Description"), node.description),
            ),
            page_size=15,
            title=tr(t"access-view-permission-catalogue = Permission catalogue"),
            empty=tr(t"access-permission-catalogue-empty = No permission nodes are registered."),
        )

    @staticmethod
    def _audit(
        rows: tuple[PermissionAuditEntry, ...],
    ) -> sp.Browser[PermissionAuditEntry, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        return sp.Browser(
            sl.sources.list_source(rows),
            key="permission-audit",
            identity=lambda row: str(row.id),
            label=lambda row: row.action,
            summary=_audit_summary,
            detail=_audit_detail,
            page_size=15,
            title=tr(t"access-view-permission-audit = Permission audit"),
            empty=tr(t"access-audit-empty = No permission changes are recorded for this server."),
        )

    async def _save(self, kind: str, values: PermissionValues, revision: int | None, label: sl.TextLike) -> None:
        await self.edits.bind(kind, PERMISSION_GRAPH, self._revision if revision is None else revision).save(
            values.edit_values(), label
        )

    async def _change_rule(
        self, event: sl.SubmitEvent, *, base_revision: int | None = None, pattern: str | None = None
    ) -> None:
        if not await self._authorize_mutation(event, PERM_GRANT_GUILD, PERM_GRANT_GLOBAL):
            return
        effect = {"allow": Effect.ALLOW, "deny": Effect.DENY, "forbid": Effect.FORBID}.get(
            str(event.values["operation"])
        )
        label = tr(t"access-view-direct-permission-rule-updated = Direct permission rule updated.")
        await self._save(
            GRANT_EDIT,
            GrantValues(
                pattern=pattern or str(event.values["pattern"]),
                effect=effect,
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
                scope_guild_id=_scope(str(event.values["scope"]), self._guild_id),
                reason=str(event.values.get("reason") or "") or None,
            ),
            base_revision,
            label,
        )
        await event.notice(label)

    async def _change_assignment(
        self, event: sl.SubmitEvent, *, base_revision: int | None = None, role: PermissionRole | None = None
    ) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        if role is None:
            role = await self._admin.role(str(event.values["slug"]), guild_id=self._guild_id)
        label = tr(t"access-view-internal-role-assignment-updated = Internal role assignment updated.")
        await self._save(
            ASSIGNMENT_EDIT,
            AssignmentValues(
                role_id=role.id,
                present=event.values["operation"] == "assign",
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
                scope_guild_id=_scope(str(event.values["scope"]), self._guild_id),
                reason=str(event.values.get("reason") or "") or None,
            ),
            base_revision,
            label,
        )
        await event.notice(label)

    async def _create_role(self, event: sl.SubmitEvent, *, base_revision: int | None = None) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        form = _CreateRoleForm.from_submission(event)
        slug = form.slug
        if not slug:
            stem = re.sub(r"[^a-z0-9]+", "-", form.name.lower()).strip("-")[:28]
            if not stem or not stem[0].isalpha():
                stem = f"role-{stem}"[:28]
            if len(stem) < 2:
                stem += "-role"
            existing = {role.slug for role in await self._admin.roles(guild_id=_scope(form.scope, self._guild_id))}
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
                guild_id=_scope(form.scope, self._guild_id),
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
        if not await self._authorize_mutation(event.source, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        slug, identity, revision = pending
        label = md_tr(t"access-view-deleted-internal-role = Deleted internal role **{slug}**.")
        await self._save(ROLE_DELETE, RoleDeletionValues(role_id=identity), revision, label)
        await event.source.notice(label)

    async def _authorize_mutation(self, event: sl.ActionEvent[Any], *nodes: PermissionNode) -> bool:
        for node in nodes:
            if await self._authorize(node):
                return True
        await event.notice(tr(t"access-change-forbidden = You are no longer allowed to change access controls."))
        return False


def _without(form: sl.forms.FormSpec[sd.Request[Any, Any]], key: str) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
    return replace(
        form, items=tuple(item for item in form.items if not isinstance(item, sl.forms.FormField) or item.key != key)
    )


def _scope_field() -> sl.forms.ChoiceField[str]:
    return sl.forms.ChoiceField(
        key="scope",
        label=tr(t"access-view-scope = Scope"),
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
    if isinstance(row, PermissionGrant):
        return f"{effect_label(row.effect)} · {scope_label(row.scope_guild_id)}"
    return f"internal role · {scope_label(row.scope_guild_id)}"


def _subject_item_detail(row: PermissionGrant | PermissionRoleAssignment) -> sl.semantic.Fields:
    if isinstance(row, PermissionGrant):
        return sl.fields(
            sl.field(tr(t"access-view-effect = Effect"), effect_label(row.effect)),
            sl.field(tr(t"access-view-pattern = Pattern"), row.pattern),
            sl.field(tr(t"access-view-scope = Scope"), scope_label(row.scope_guild_id)),
            sl.field(tr(t"access-view-reason = Reason"), row.reason or "—"),
        )
    return sl.fields(
        sl.field(tr(t"access-view-internal-role = Internal role"), row.role_slug),
        sl.field(tr(t"access-view-scope = Scope"), scope_label(row.scope_guild_id)),
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
            tr(t"access-view-composed-roles = Composed roles"),
            ", ".join(by_id[item] for item in role.includes_roles if item in by_id) or "—",
        ),
    )


def _audit_summary(row: PermissionAuditEntry) -> str:
    return f"{row.at} · {row.pattern or 'role change'}"


def _audit_detail(row: PermissionAuditEntry) -> sl.semantic.Fields:
    return sl.fields(
        sl.field(tr(t"access-view-action = Action"), row.action),
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
        label=tr(t"access-view-operation = Operation"),
        options=(
            sl.forms.ChoiceOption("allow", tr(t"access-view-allow = Allow"), "allow"),
            sl.forms.ChoiceOption("deny", tr(t"access-view-deny = Deny"), "deny"),
            sl.forms.ChoiceOption("forbid", tr(t"access-view-forbid = Forbid"), "forbid"),
            sl.forms.ChoiceOption("revoke", tr(t"access-view-revoke = Revoke"), "revoke"),
        ),
    )
    pattern = sl.forms.TextField(label=tr(t"access-view-permission-pattern = Permission pattern"), maximum=200)
    scope = _scope_field()
    reason = sl.forms.TextField(label=tr(t"access-view-reason = Reason"), required=False, maximum=500)


class _AssignmentForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-view-change-internal-role-assignment = Change internal role assignment")

    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = Operation"),
        options=(
            sl.forms.ChoiceOption("assign", tr(t"access-view-assign = Assign"), "assign"),
            sl.forms.ChoiceOption("unassign", tr(t"access-view-unassign = Unassign"), "unassign"),
        ),
    )
    slug = sl.forms.TextField(label=tr(t"access-view-internal-role-slug = Internal role slug"), maximum=100)
    scope = _scope_field()
    reason = sl.forms.TextField(label=tr(t"access-view-reason = Reason"), required=False, maximum=500)


class _CreateRoleForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"access-view-create-internal-role = Create internal role")

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
    title = tr(t"access-custom-pattern = Custom permission pattern")

    pattern = sl.forms.TextField(
        label=tr(t"access-view-permission-pattern = Permission pattern"),
        description=tr(
            t"access-custom-pattern-help = For example, build.*.view matches view permissions across build resources."
        ),
        maximum=200,
    )
    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = Operation"),
        options=tuple(sl.forms.ChoiceOption(key, label, key) for key, label in _ROLE_ACTIONS[:3]),
    )
