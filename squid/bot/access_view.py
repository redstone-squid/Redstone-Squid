"""Canonical permission and internal-role administration workspace."""

from collections.abc import Awaitable, Callable
from typing import Any, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.core.actors import Actor
from squid.permissions.application.administration import (
    EXCLUDE_MODE,
    INCLUDE_MODE,
    PermissionAdministrationService,
    effect_label,
    scope_label,
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
from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment

type ActorProvider = Callable[[], Awaitable[Actor]]
type AccessAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class AccessScreen(sd.Screen):
    """Browse and administer one subject's access in a guild.

    Initial capabilities determine which tabs are displayed. Mutation handlers
    recheck authorization and obtain a fresh administrative actor; the application
    service then enforces role rank, scope, and delegation limits.
    """

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("access"))

    def __init__(
        self,
        admin: PermissionAdministrationService,
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
        self._actor = actor
        self._authorize = authorize
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target, sd.Request] | None = None
        self._pending_delete: str | None = None
        self._decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target, sd.Request] | None = None

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        tabs: list[sp.Tab[sl.ComponentsV2Target, sd.Request]] = []
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
        self._tabs = sp.Tabs(tabs, key="access-tabs", title=tr(t"access-view-access-for = Access for {subject_label}"))

    @property
    def _may_grant(self) -> bool:
        return PERM_GRANT_GUILD in self._capabilities or PERM_GRANT_GLOBAL in self._capabilities

    @property
    def _may_manage_roles(self) -> bool:
        return ROLE_DEFINITION_MANAGE_GUILD in self._capabilities or ROLE_DEFINITION_MANAGE in self._capabilities

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._pending_delete is not None and self._decision is not None:
            slug = self._pending_delete
            return (
                sl.section(
                    sl.heading(tr(t"access-view-delete-internal-role = Delete internal role")),
                    sl.paragraph(
                        tr(t"access-view-delete-and-its-assignments = Delete **{slug}** and its assignments?")
                    ),
                ),
                self._decision.keyed("delete-decision"),
            )
        if self._tabs is None:
            return (sl.status(tr(t"access-view-loading-access-controls = Loading access controls.")),)
        return (self._tabs.keyed("tabs"),)

    def _subject_browser(
        self, rules: tuple[PermissionGrant, ...], assignments: tuple[PermissionRoleAssignment, ...]
    ) -> sp.Browser[PermissionGrant | PermissionRoleAssignment, sl.ComponentsV2Target, sd.Request]:
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

    def _subject_forms(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        if self._may_grant:
            nodes.append(
                sl.form(
                    tr(t"access-view-change-direct-rule = Change direct rule"),
                    self._rule_form(),
                    key="rule",
                    on_submit=self._change_rule,
                )
            )
        if self._may_manage_roles:
            nodes.append(
                sl.form(
                    tr(t"access-view-change-role-assignment = Change role assignment"),
                    self._assignment_form(),
                    key="assignment",
                    on_submit=self._change_assignment,
                )
            )
        return tuple(nodes)

    @staticmethod
    def _rule_form() -> sl.forms.FormSpec:
        return _RuleForm().spec()

    @staticmethod
    def _assignment_form() -> sl.forms.FormSpec:
        return _AssignmentForm().spec()

    @staticmethod
    def _role_browser(
        roles: tuple[PermissionRole, ...],
    ) -> sp.Browser[PermissionRole, sl.ComponentsV2Target, sd.Request]:
        return sp.Browser(
            sl.sources.list_source(roles),
            key="roles",
            identity=lambda role: str(role.id),
            label=lambda role: role.slug,
            summary=_role_summary,
            detail=lambda role: _role_detail(role, roles),
            page_size=15,
            title=tr(t"access-view-internal-roles = Internal roles"),
            empty=tr(t"access-role-list-empty = No internal roles are visible in this server."),
        )

    def _role_forms(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"access-view-create-internal-role = Create internal role"),
                self._create_role_form(),
                key="create-role",
                on_submit=self._create_role,
            ),
            sl.form(
                tr(t"access-view-edit-internal-role = Edit internal role"),
                self._edit_role_form(),
                key="edit-role",
                on_submit=self._edit_role,
            ),
        )

    @staticmethod
    def _create_role_form() -> sl.forms.FormSpec:
        return _CreateRoleForm().spec()

    @staticmethod
    def _edit_role_form() -> sl.forms.FormSpec:
        return _EditRoleForm().spec()

    @staticmethod
    def _catalogue() -> sp.Browser[Any, sl.ComponentsV2Target, sd.Request]:
        nodes = tuple(CATALOGUE)
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
    ) -> sp.Browser[PermissionAuditEntry, sl.ComponentsV2Target, sd.Request]:
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

    async def _change_rule(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, PERM_GRANT_GUILD, PERM_GRANT_GLOBAL):
            return
        form = _RuleForm.from_submission(event)
        operation = form.operation
        pattern = form.pattern
        scope = _scope(form.scope, self._guild_id)
        actor = await self._actor()
        if operation == "revoke":
            await self._admin.revoke(
                actor,
                pattern=pattern,
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                scope_guild_id=scope,
            )
        else:
            effect = {"allow": Effect.ALLOW, "deny": Effect.DENY, "forbid": Effect.FORBID}[operation]
            await self._admin.grant(
                actor,
                pattern=pattern,
                effect=effect,
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
                scope_guild_id=scope,
                reason=form.reason or None,
            )
        await self._refresh()
        await event.notice(tr(t"access-view-direct-permission-rule-updated = Direct permission rule updated."))

    async def _change_assignment(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        form = _AssignmentForm.from_submission(event)
        operation = form.operation
        slug = form.slug
        scope = _scope(form.scope, self._guild_id)
        actor = await self._actor()
        role = await self._admin.role(slug, guild_id=self._guild_id)
        if operation == "assign":
            await self._admin.assign(
                actor,
                role,
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                guild_id=self._guild_id,
                scope_guild_id=scope,
                reason=form.reason or None,
            )
        else:
            await self._admin.unassign(
                actor,
                role,
                account_id=self._account_id,
                discord_role_id=self._discord_role_id,
                scope_guild_id=scope,
            )
        await self._refresh()
        await event.notice(tr(t"access-view-internal-role-assignment-updated = Internal role assignment updated."))

    async def _create_role(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        form = _CreateRoleForm.from_submission(event)
        slug = form.slug
        await self._admin.create_role(
            await self._actor(),
            slug=slug,
            name=form.name,
            rank=form.rank,
            guild_id=_scope(form.scope, self._guild_id),
        )
        await self._refresh()
        await event.notice(tr(t"access-view-created-internal-role = Created internal role **{slug}**."))

    async def _edit_role(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        form = _EditRoleForm.from_submission(event)
        operation = form.operation
        slug = form.slug
        value = form.value or ""
        actor = await self._actor()
        role = await self._admin.role(slug, guild_id=self._guild_id)
        if operation == "delete":
            self._pending_delete = slug
            self._decision = sp.Decision[sl.ComponentsV2Target, sd.Request](
                tr(t"access-role-delete-warning = Deleting an internal role removes every assignment of it."),
                (
                    sp.DecisionOption("confirm", tr(t"access-view-delete-role = Delete role"), sl.Tone.DANGER),
                    sp.DecisionOption("cancel", tr(t"access-cancel = Cancel")),
                ),
                key="delete-role",
            ).build_component(on_decide=self._decide_delete)
            return
        if not value:
            await event.notice(tr(t"access-value-required = This operation needs a value."))
            return
        if operation in {"include", "exclude"}:
            await self._admin.add_pattern(
                actor, role, value, mode=INCLUDE_MODE if operation == "include" else EXCLUDE_MODE
            )
        elif operation == "remove-pattern":
            await self._admin.remove_pattern(actor, role, value)
        elif operation in {"compose", "decompose"}:
            included = await self._admin.role(value, guild_id=self._guild_id)
            if operation == "compose":
                await self._admin.add_include(actor, role, included)
            else:
                await self._admin.remove_include(actor, role, included)
        elif operation == "rank":
            await self._admin.set_rank(actor, role, int(value))
        await self._refresh()
        await event.notice(tr(t"access-view-internal-role-updated = Internal role updated."))

    async def _decide_delete(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        """Recheck authority and reload the role after the user confirms deletion."""
        slug = self._pending_delete
        self._pending_delete = None
        self._decision = None
        if slug is None or choice == "cancel":
            return
        if not await self._authorize_mutation(event.source, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        role = await self._admin.role(slug, guild_id=self._guild_id)
        await self._admin.delete_role(await self._actor(), role)
        await self._refresh()
        await event.source.notice(tr(t"access-view-deleted-internal-role = Deleted internal role **{slug}**."))

    async def _authorize_mutation(self, event: sl.ActionEvent, *nodes: PermissionNode) -> bool:
        for node in nodes:
            if await self._authorize(node):
                return True
        await event.notice(tr(t"access-change-forbidden = You are no longer allowed to change access controls."))
        return False


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


class _RuleForm(sl.forms.Form):
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


class _AssignmentForm(sl.forms.Form):
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


class _CreateRoleForm(sl.forms.Form):
    title = tr(t"access-view-create-internal-role = Create internal role")

    slug = sl.forms.TextField(label=tr(t"access-view-slug = Slug"), maximum=100)
    name = sl.forms.TextField(label=tr(t"access-view-display-name = Display name"), maximum=100)
    rank = sl.forms.IntField(
        label=tr(t"access-view-management-rank = Management rank"), default=0, minimum=0, maximum=999
    )
    scope = _scope_field()


class _EditRoleForm(sl.forms.Form):
    title = tr(t"access-view-edit-internal-role = Edit internal role")

    operation = sl.forms.ChoiceField(
        label=tr(t"access-view-operation = Operation"),
        options=tuple((sl.forms.ChoiceOption(key, label, key) for key, label in _ROLE_ACTIONS)),
    )
    slug = sl.forms.TextField(label=tr(t"access-view-role-slug = Role slug"), maximum=100)
    value = sl.forms.TextField(
        label=tr(t"access-view-pattern-included-role-or-rank = Pattern, included role, or rank"),
        required=False,
        maximum=200,
    )
