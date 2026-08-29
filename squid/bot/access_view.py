"""Canonical permission and internal-role administration workspace."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import L
from squid.permissions.application.administration import (
    EXCLUDE_MODE,
    INCLUDE_MODE,
    Actor,
    PermissionAdministrationService,
    effect_label,
    scope_label,
)
from squid.permissions.application.ports import AssignmentRow, AuditRow, RoleRecord, RuleRow
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

type ActorProvider = Callable[[], Awaitable[Actor]]
type AccessAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class AccessScreen(sd.UserSessionScreen):
    """A guild access workspace that ends when closed, replaced, or timed out."""

    session_name = "access"
    timeout = 300

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
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None
        self._pending_delete: str | None = None
        self._decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target] | None = None

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        tabs: list[sp.Tab[sl.ComponentsV2Target]] = []
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
            tabs.append(sp.Tab("subject", L(t"Subject"), self._subject_browser(rules, assignments)))
            subject_forms = self._subject_forms()
            if subject_forms:
                tabs.append(sp.Tab("assignments", L(t"Rules and assignments"), subject_forms))
        if self._may_manage_roles:
            roles = await self._admin.roles(guild_id=self._guild_id)
            tabs.append(sp.Tab("roles", L(t"Internal roles"), self._role_browser(roles)))
            tabs.append(sp.Tab("role-editor", L(t"Edit roles"), self._role_forms()))
        if PERM_NODE_VIEW in self._capabilities:
            tabs.append(sp.Tab("catalogue", L(t"Catalogue"), self._catalogue()))
        if PERM_AUDIT_VIEW in self._capabilities:
            audit = await self._admin.audit(guild_id=self._guild_id, limit=50)
            tabs.append(sp.Tab("audit", L(t"Audit"), self._audit(audit)))
        subject_label = self._subject_label
        self._tabs = sp.Tabs(tabs, key="access-tabs", title=L(t"Access for {subject_label}")).build_component()

    @property
    def _may_grant(self) -> bool:
        return PERM_GRANT_GUILD in self._capabilities or PERM_GRANT_GLOBAL in self._capabilities

    @property
    def _may_manage_roles(self) -> bool:
        return ROLE_DEFINITION_MANAGE_GUILD in self._capabilities or ROLE_DEFINITION_MANAGE in self._capabilities

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._pending_delete is not None and self._decision is not None:
            slug = self._pending_delete
            return (
                sl.section(
                    sl.heading(L(t"Delete internal role")), sl.paragraph(L(t"Delete **{slug}** and its assignments?"))
                ),
                self.boundary(self._decision, key="delete-decision"),
            )
        if self._tabs is None:
            return (sl.status(L(t"Loading access controls.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(L(t"Close"), self._close, key="close"), key="access-actions"),
        )

    def _subject_browser(
        self, rules: tuple[RuleRow, ...], assignments: tuple[AssignmentRow, ...]
    ) -> sp.Browser[RuleRow | AssignmentRow, sl.ComponentsV2Target]:
        return sp.Browser(
            sl.sources.list_source((*rules, *assignments)),
            key="subject-access",
            identity=lambda row: f"{type(row).__name__}:{row.id}",
            label=_subject_item_label,
            summary=_subject_item_summary,
            detail=_subject_item_detail,
            page_size=15,
            title=L(t"Rules and assignments"),
            empty=L(t"This subject has no explicit access rules or internal roles."),
        )

    def _subject_forms(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if self._may_grant:
            nodes.append(sl.form(L(t"Change direct rule"), self._rule_form(), key="rule", on_submit=self._change_rule))
        if self._may_manage_roles:
            nodes.append(
                sl.form(
                    L(t"Change role assignment"),
                    self._assignment_form(),
                    key="assignment",
                    on_submit=self._change_assignment,
                )
            )
        return tuple(nodes)

    @staticmethod
    def _rule_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Change direct permission rule"),
            (
                sl.forms.ChoiceField(
                    key="operation",
                    label=L(t"Operation"),
                    options=(
                        sl.forms.ChoiceOption("allow", L(t"Allow"), "allow"),
                        sl.forms.ChoiceOption("deny", L(t"Deny"), "deny"),
                        sl.forms.ChoiceOption("forbid", L(t"Forbid"), "forbid"),
                        sl.forms.ChoiceOption("revoke", L(t"Revoke"), "revoke"),
                    ),
                ),
                sl.forms.TextField(key="pattern", label=L(t"Permission pattern"), maximum=200),
                _scope_field(),
                sl.forms.TextField(key="reason", label=L(t"Reason"), required=False, maximum=500),
            ),
        )

    @staticmethod
    def _assignment_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Change internal role assignment"),
            (
                sl.forms.ChoiceField(
                    key="operation",
                    label=L(t"Operation"),
                    options=(
                        sl.forms.ChoiceOption("assign", L(t"Assign"), "assign"),
                        sl.forms.ChoiceOption("unassign", L(t"Unassign"), "unassign"),
                    ),
                ),
                sl.forms.TextField(key="slug", label=L(t"Internal role slug"), maximum=100),
                _scope_field(),
                sl.forms.TextField(key="reason", label=L(t"Reason"), required=False, maximum=500),
            ),
        )

    @staticmethod
    def _role_browser(roles: tuple[RoleRecord, ...]) -> sp.Browser[RoleRecord, sl.ComponentsV2Target]:
        return sp.Browser(
            sl.sources.list_source(roles),
            key="roles",
            identity=lambda role: str(role.id),
            label=lambda role: role.slug,
            summary=_role_summary,
            detail=lambda role: _role_detail(role, roles),
            page_size=15,
            title=L(t"Internal roles"),
            empty=L(t"No internal roles are visible in this server."),
        )

    def _role_forms(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                L(t"Create internal role"), self._create_role_form(), key="create-role", on_submit=self._create_role
            ),
            sl.form(L(t"Edit internal role"), self._edit_role_form(), key="edit-role", on_submit=self._edit_role),
        )

    @staticmethod
    def _create_role_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Create internal role"),
            (
                sl.forms.TextField(key="slug", label=L(t"Slug"), maximum=100),
                sl.forms.TextField(key="name", label=L(t"Display name"), maximum=100),
                sl.forms.IntField(key="rank", label=L(t"Management rank"), default=0, minimum=0, maximum=999),
                _scope_field(),
            ),
        )

    @staticmethod
    def _edit_role_form() -> sl.forms.FormSpec:
        actions = (
            ("include", L(t"Include pattern")),
            ("exclude", L(t"Exclude pattern")),
            ("remove-pattern", L(t"Remove pattern")),
            ("compose", L(t"Include role")),
            ("decompose", L(t"Remove included role")),
            ("rank", L(t"Set rank")),
            ("delete", L(t"Delete role")),
        )
        return sl.forms.FormSpec(
            L(t"Edit internal role"),
            (
                sl.forms.ChoiceField(
                    key="operation",
                    label=L(t"Operation"),
                    options=tuple(sl.forms.ChoiceOption(key, label, key) for key, label in actions),
                ),
                sl.forms.TextField(key="slug", label=L(t"Role slug"), maximum=100),
                sl.forms.TextField(
                    key="value", label=L(t"Pattern, included role, or rank"), required=False, maximum=200
                ),
            ),
        )

    @staticmethod
    def _catalogue() -> sp.Browser[Any, sl.ComponentsV2Target]:
        nodes = tuple(CATALOGUE)
        return sp.Browser(
            sl.sources.list_source(nodes),
            key="permission-catalogue",
            identity=lambda node: node.name,
            label=lambda node: node.name,
            summary=lambda node: f"{node.scope.value} · {node.default.value}",
            detail=lambda node: sl.fields(
                sl.field(L(t"Scope"), node.scope.value),
                sl.field(L(t"Default"), node.default.value),
                sl.field(L(t"Description"), node.description),
            ),
            page_size=15,
            title=L(t"Permission catalogue"),
            empty=L(t"No permission nodes are registered."),
        )

    @staticmethod
    def _audit(rows: tuple[AuditRow, ...]) -> sp.Browser[AuditRow, sl.ComponentsV2Target]:
        return sp.Browser(
            sl.sources.list_source(rows),
            key="permission-audit",
            identity=lambda row: str(row.id),
            label=lambda row: row.action,
            summary=_audit_summary,
            detail=_audit_detail,
            page_size=15,
            title=L(t"Permission audit"),
            empty=L(t"No permission changes are recorded for this server."),
        )

    async def _change_rule(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, PERM_GRANT_GUILD, PERM_GRANT_GLOBAL):
            return
        operation = cast(str, event.values["operation"])
        pattern = cast(str, event.values["pattern"])
        scope = _scope(cast(str, event.values["scope"]), self._guild_id)
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
                reason=cast(str | None, event.values.get("reason")) or None,
            )
        await self._refresh()
        await event.notice(L(t"Direct permission rule updated."))

    async def _change_assignment(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        operation = cast(str, event.values["operation"])
        slug = cast(str, event.values["slug"])
        scope = _scope(cast(str, event.values["scope"]), self._guild_id)
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
                reason=cast(str | None, event.values.get("reason")) or None,
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
        await event.notice(L(t"Internal role assignment updated."))

    async def _create_role(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        slug = cast(str, event.values["slug"])
        await self._admin.create_role(
            await self._actor(),
            slug=slug,
            name=cast(str, event.values["name"]),
            rank=cast(int, event.values["rank"]),
            guild_id=_scope(cast(str, event.values["scope"]), self._guild_id),
        )
        await self._refresh()
        await event.notice(L(t"Created internal role **{slug}**."))

    async def _edit_role(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_mutation(event, ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE):
            return
        operation = cast(str, event.values["operation"])
        slug = cast(str, event.values["slug"])
        value = cast(str | None, event.values.get("value")) or ""
        actor = await self._actor()
        role = await self._admin.role(slug, guild_id=self._guild_id)
        if operation == "delete":
            self._pending_delete = slug
            self._decision = sp.Decision[sl.ComponentsV2Target](
                L(t"Deleting an internal role removes every assignment of it."),
                (
                    sp.DecisionOption("confirm", L(t"Delete role"), sl.Tone.DANGER),
                    sp.DecisionOption("cancel", L(t"Cancel")),
                ),
                key="delete-role",
            ).build_component(on_decide=self._decide_delete)
            return
        if not value:
            await event.notice(L(t"This operation needs a value."))
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
        await event.notice(L(t"Internal role updated."))

    async def _decide_delete(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
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
        await event.source.notice(L(t"Deleted internal role **{slug}**."))

    async def _authorize_mutation(self, event: sl.ActionEvent, *nodes: PermissionNode) -> bool:
        for node in nodes:
            if await self._authorize(node):
                return True
        await event.notice(L(t"You are no longer allowed to change access controls."))
        return False

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


def _scope_field() -> sl.forms.ChoiceField[str]:
    return sl.forms.ChoiceField(
        key="scope",
        label=L(t"Scope"),
        default="guild",
        options=(
            sl.forms.ChoiceOption("guild", L(t"This server"), "guild"),
            sl.forms.ChoiceOption("global", L(t"Everywhere"), "global"),
        ),
    )


def _scope(value: str, guild_id: int) -> int | None:
    return None if value == "global" else guild_id


def _subject_item_label(row: RuleRow | AssignmentRow) -> str:
    return row.pattern if isinstance(row, RuleRow) else row.role_slug


def _subject_item_summary(row: RuleRow | AssignmentRow) -> str:
    if isinstance(row, RuleRow):
        return f"{effect_label(row.effect)} · {scope_label(row.scope_guild_id)}"
    return f"internal role · {scope_label(row.scope_guild_id)}"


def _subject_item_detail(row: RuleRow | AssignmentRow) -> sl.semantic.Fields:
    if isinstance(row, RuleRow):
        return sl.fields(
            sl.field(L(t"Effect"), effect_label(row.effect)),
            sl.field(L(t"Pattern"), row.pattern),
            sl.field(L(t"Scope"), scope_label(row.scope_guild_id)),
            sl.field(L(t"Reason"), row.reason or "—"),
        )
    return sl.fields(
        sl.field(L(t"Internal role"), row.role_slug),
        sl.field(L(t"Scope"), scope_label(row.scope_guild_id)),
    )


def _role_summary(role: RoleRecord) -> str:
    scope = "global" if role.guild_id is None else "this server"
    protected = " · built-in" if role.protected else ""
    return f"rank {role.rank} · {scope}{protected}"


def _role_detail(role: RoleRecord, roles: tuple[RoleRecord, ...]) -> sl.semantic.Fields:
    by_id = {item.id: item.slug for item in roles}
    return sl.fields(
        sl.field(L(t"Includes"), ", ".join(role.includes) or "—"),
        sl.field(L(t"Excludes"), ", ".join(role.excludes) or "—"),
        sl.field(L(t"Composed roles"), ", ".join(by_id[item] for item in role.includes_roles if item in by_id) or "—"),
    )


def _audit_summary(row: AuditRow) -> str:
    return f"{row.at} · {row.pattern or 'role change'}"


def _audit_detail(row: AuditRow) -> sl.semantic.Fields:
    return sl.fields(
        sl.field(L(t"Action"), row.action),
        sl.field(L(t"Pattern"), row.pattern or "—"),
        sl.field(L(t"Reason"), row.reason or "—"),
        sl.field(L(t"Actor account"), str(row.actor_account_id or "—")),
    )
