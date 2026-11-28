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
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditRunner
from squid.core.permissions.application.administration import PermissionAdministrationService, effect_label
from squid.core.permissions.application.edits import (
    ASSIGNMENT_EDIT,
    BULK_ASSIGNMENT_EDIT,
    GRANT_EDIT,
    INCLUDE_EDIT,
    PATTERN_EDIT,
    PERMISSION_GRAPH,
    RANK_EDIT,
    ROLE_CREATE,
    ROLE_DELETE,
    AssignmentBatch,
    AssignmentValues,
    GrantValues,
    IncludeValues,
    PatternValues,
    PermissionValues,
    RankValues,
    RoleDeletionValues,
    RolePresenceValues,
)
from squid.core.permissions.domain import CATALOGUE, Decision, Effect, Reason
from squid.core.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid.core.permissions.domain.models import (
    GuildListFilters,
    NodeScope,
    PatternMode,
    PermissionSubjectFilters,
    RolePermissionSubject,
)
from squid.core.permissions.models import (
    PermissionAuditEntry,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
)
from squid.core.suggestions.domain import Suggestion
from squid_i18n import md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target

type ActorProvider = Callable[[], Awaitable[Actor]]

_DENIED = tr(t"access-change-forbidden = You are no longer allowed to change access controls.")


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessTarget:
    account_id: int | None
    discord_role_id: int | None
    scope_guild_id: int | None
    subject: str
    member_id: int | None
    revision: int


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
    _recipients: tuple[sl.entity.EntityRef, ...] = sl.state(())
    _rank: int = sl.state(0)
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
        self._actor = actor
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
        if PERM_SUBJECT_INSPECT in self.grants or self._may_grant or self._may_manage_roles:
            self._page = "edit"
        await self._refresh()

    async def _refresh(self) -> None:
        self._revision = await self._admin.revision(context=ActingContext.for_actor(await self._actor()))
        page = self._page
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        tasks = (
            (
                "edit",
                tr(t"access-subject = Member or role access"),
                PERM_SUBJECT_INSPECT in self.grants or self._may_grant or self._may_manage_roles,
            ),
            ("roles", tr(t"access-view-internal-roles = Manage bot roles"), self._may_manage_roles),
            ("audit", tr(t"access-history = Audit log"), PERM_AUDIT_VIEW in self.grants),
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
        elif page == "edit":
            nodes.extend(self._target_controls())
            if not self._target_chosen:
                nodes.append(
                    sl.note(
                        tr(
                            t"access-ui-select-the-member-or-discord-role-whose-access-you-want-to-manage = Select the member or Discord role whose access you want to manage."
                        )
                    )
                )
            else:
                if PERM_SUBJECT_INSPECT in self.grants and self._bot and await self.grants.allows(PERM_SUBJECT_INSPECT):
                    nodes.extend(await self._result())
                nodes.extend(self._subject_forms())
                if not self._may_grant and PERM_SUBJECT_INSPECT in self.grants:
                    nodes.append(
                        sl.action_controls(
                            sl.action_control(
                                tr(t"access-find = Find a bot action"), self._choose_permission, key="action"
                            ),
                            key="find-action",
                        )
                    )
        elif page == "recipients" and self._selected_role:
            role, revision = self._selected_role
            nodes.extend(
                (
                    sl.heading(role.name),
                    sl.entities(
                        key="recipients",
                        entity_type=sl.entity.EntityType.MENTIONABLE,
                        maximum=25,
                        selection=self._recipients,
                        on_change=self._select_recipients,
                        placeholder=tr(t"access-recipients = Select members and Discord roles"),
                    ),
                )
            )
            if self._recipients:
                names = self._recipient_names()
                nodes.append(sl.note(", ".join(names)))
                nodes.append(
                    sl.form(
                        tr(t"access-apply-role = Apply role assignment"),
                        self._batch_form(role, names),
                        key="apply-role",
                        on_submit=partial(self._apply_role, role, revision, self._recipients),
                    )
                )
        elif page == "roles":
            if self._bot:
                self._rank = (
                    await self._admin.resolve_actor(context=ActingContext.for_actor(await self._actor()))
                ).require_permission_rank()
            nodes.append(sl.note(f"Your management rank: {self._rank}"))
            nodes.extend(self._role_forms())
            nodes.append(
                self._role_browser(
                    await self._admin.roles(
                        context=ActingContext.for_actor(await self._actor()),
                        filters=GuildListFilters.for_guild(self._guild_id),
                    )
                )
            )
        elif page in {"role-settings", "role-actions"}:
            roles = await self._admin.roles(
                context=ActingContext.for_actor(await self._actor()), filters=GuildListFilters.for_guild(self._guild_id)
            )
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
                nodes.append(
                    await self._audit(
                        await self._admin.audit(
                            context=ActingContext.for_actor(await self._actor()), guild_id=self._guild_id, limit=50
                        )
                    )
                )
            else:
                nodes.append(sl.note(_DENIED))
        if page in {"edit", "reference"} and (self._selected_role or self._selected_pattern):
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"access-ui-back = Back"), self._go_back, key="back-selection"),
                    key="back-controls",
                )
            )
        if page != "home" and any(allowed and key != page for key, _, allowed in tasks):
            nodes.append(
                sl.action_controls(
                    *(
                        sl.action_control(label, partial(self._open, key), key="nav-" + key)
                        for key, label, allowed in tasks
                        if allowed and key != page
                    ),
                    key="navigation",
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
        assert self._bot
        guild = self._bot.get_guild(self._guild_id)
        if guild is None:
            return (sl.note(tr(t"access-ui-this-server-is-unavailable = This server is unavailable.")),)
        if self._discord_role_id is not None:
            if guild.get_role(self._discord_role_id) is None:
                return (
                    sl.note(tr(t"access-ui-that-discord-role-has-been-deleted = That Discord role has been deleted.")),
                )
            actor = RolePermissionSubject(guild_id=guild.id, discord_role_ids=frozenset({self._discord_role_id}))
        else:
            assert self._member_id is not None
            try:
                member = await guild.fetch_member(self._member_id)
            except discord.NotFound:
                return (sl.note(tr(t"access-ui-this-member-has-left-the-server = This member has left the server.")),)
            actor = await self._bot.actors.resolve(member, account_id=self._account_id)
        global_admin = await self.grants.allows(PERM_GRANT_GLOBAL, ROLE_DEFINITION_MANAGE)
        visible = tuple(n.name for n in CATALOGUE.values() if global_admin or n.scope is NodeScope.GUILD)
        decisions = (await self._bot.services.permissions.check(actor, *visible)).decisions
        role_only = self._discord_role_id is not None
        allowed = tuple(d for d in decisions if d.allowed and (not role_only or d.decisive_rule))
        target = PermissionSubjectFilters.for_subject(self._account_id, self._discord_role_id)
        rules = await self._admin.rules_for(context=ActingContext.for_actor(await self._actor()), filters=target)
        assignments = tuple(
            row
            for row in await self._admin.assignments_for(
                context=ActingContext.for_actor(await self._actor()), filters=target
            )
            if global_admin or row.scope_guild_id in (None, self._guild_id)
        )
        rows = tuple(
            r
            for r in rules
            if global_admin
            or (
                r.scope_guild_id in (None, self._guild_id)
                and (any(n.scope is NodeScope.GUILD for n in CATALOGUE.expand(r.pattern)))
            )
        )
        results: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sp.Browser(
                sl.sources.list_source(allowed),
                key="privileges",
                identity=lambda d: d.node,
                label=lambda d: CATALOGUE[d.node].description,
                summary=lambda d: self._privilege_summary(d, guild),
                detail=lambda d: sl.paragraph(_explanation(d, role_only)),
                fetch_size=5,
                title=f"Effective privileges in {self._guild_name} ({len(allowed)})",
                empty="No allowed actions in your inspection scope.",
            ),
        ]
        if rows or assignments:
            authors = await self._account_names(
                {r.granted_by_account_id for r in (*rows, *assignments) if r.granted_by_account_id is not None}
            )
            admin_account = (await self._actor()).linked_account_id
            if admin_account is not None:
                authors[admin_account] = "you"
            results.append(self._subject_browser(rows, assignments, authors))
        else:
            results.append(sl.note("No directly assigned permissions or bot roles."))
        selected = next((d for d in decisions if d.node == self._action), None)
        if selected:
            results.append(sl.paragraph(_explanation(selected, role_only)))
        return tuple(results)

    @staticmethod
    def _privilege_summary(decision: Decision, guild: discord.Guild) -> sl.TextLike:
        action = CATALOGUE[decision.node].description
        rule = decision.decisive_rule
        if rule is None:
            source = "Bot owner" if decision.reason is Reason.OWNER else "Default"
        else:
            kind, _, identity = rule.source.partition(":")
            source = {"role": f"Bot role {identity}", "account": "Direct rule", "discord": "Manage Server"}.get(
                kind, "Discord role"
            )
            role_id = rule.via or (rule.source if kind == "discord-role" else "")
            if role_id.startswith("discord-role:"):
                discord_role = guild.get_role(int(role_id.split(":")[1]))
                if discord_role:
                    source = f"{source} · {discord_role.name}"
        return md_tr(t"access-effective-source = {action} — {source}")

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
                on_change=self._change_subject,
                placeholder=tr(t"access-change-subject = Change member or Discord role")
                if self._target_chosen
                else tr(t"access-target-picker = Choose a member or Discord role"),
            ),
            *(
                (
                    sl.choices(
                        sl.choice(tr(t"access-view-this-server = This server"), key="guild"),
                        sl.choice(tr(t"access-all-servers = All servers"), key="global"),
                        key="access-scope",
                        selection=(self._scope_key,),
                        on_change=self._change_scope,
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
        self,
        rules: tuple[PermissionGrant, ...],
        assignments: tuple[PermissionRoleAssignment, ...],
        authors: dict[int, str],
    ) -> sp.Browser[PermissionGrant | PermissionRoleAssignment, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        def scope(row: PermissionGrant | PermissionRoleAssignment) -> str:
            if row.scope_guild_id is None:
                return "All servers"
            if row.scope_guild_id == self._guild_id:
                return self._guild_name
            guild = self._bot.get_guild(row.scope_guild_id) if self._bot else None
            return guild.name if guild else "Another server"

        return sp.Browser(
            sl.sources.list_source((*rules, *assignments)),
            key="subject-access",
            identity=lambda row: f"{type(row).__name__}:{row.id}",
            label=_subject_item_label,
            summary=lambda row: (
                f"{_subject_item_summary(row, scope(row))} · by {authors.get(row.granted_by_account_id or 0, 'System' if row.granted_by_account_id is None else 'Unknown account')}"
            ),
            detail=lambda row: _subject_item_detail(row, scope(row)),
            fetch_size=4,
            title=tr(t"access-view-rules-and-assignments = Direct rules and assigned bot roles"),
        )

    def _access_target(self, revision: int) -> AccessTarget:
        return AccessTarget(
            account_id=self._account_id,
            discord_role_id=self._discord_role_id,
            scope_guild_id=self._scope_id,
            subject=self._subject_label,
            member_id=self._member_id,
            revision=revision,
        )

    def _subject_forms(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if not (self._may_grant or self._may_manage_roles):
            return ()
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
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
                        target=self._access_target(self._revision),
                    ),
                ),
                key="subject-forms",
            ),
        ]
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
                            target=self._access_target(revision),
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
                    sl.action_controls(
                        sl.action_control(
                            tr(t"access-bulk-assign = Choose multiple recipients"),
                            partial(self._assign_selected, role),
                            key="multi-assign",
                        ),
                        key="recipient-actions",
                    ),
                    sl.form(
                        tr(t"access-view-change-role-assignment = Assign or remove this role"),
                        _without(_AssignmentForm().spec(), "slug"),
                        key="assignment",
                        on_submit=partial(
                            self._change_assignment,
                            target=self._access_target(revision),
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
                (
                    role
                    for role in await self._admin.roles(
                        context=ActingContext.for_actor(await self._actor()),
                        filters=GuildListFilters.for_guild(self._guild_id),
                    )
                    if str(role.id) == item.value
                ),
                None,
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
            fetch_size=8,
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
                        tr(t"access-ui-assign-or-remove-this-role = Assign to members or Discord roles"),
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
                                description=f"Your management rank is {self._rank}.",
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
        self._page = "recipients"
        self._recipients = ()
        self._action = None
        self._selected_pattern = None
        self._selected_role = (role, self._revision)
        await self._refresh()

    def _recipient_names(self) -> tuple[str, ...]:
        guild = self._bot.get_guild(self._guild_id) if self._bot else None
        names = []
        for ref in self._recipients:
            if ref.kind is sl.entity.EntityKind.ROLE:
                role = guild.get_role(int(ref.id)) if guild else None
                names.append(role.name if role else f"<@&{ref.id}>")
            else:
                member = guild.get_member(int(ref.id)) if guild else None
                names.append(member.display_name if member else f"<@{ref.id}>")
        return tuple(names)

    async def _select_recipients(self, event: sl.EntityEvent) -> None:
        self._recipients = tuple(event.selected)
        await self._refresh()

    def _batch_form(self, role: PermissionRole, names: tuple[str, ...]) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        fields = _AssignmentForm().spec()
        items = tuple(
            field for field in fields.items if not isinstance(field, sl.forms.FormField) or field.key != "slug"
        )
        if PERM_GRANT_GLOBAL in self.grants:
            items += (_scope_field(),)
        return replace(fields, title=f"{role.name[:24]} · {len(names)} recipients", items=items)

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _apply_role(
        self,
        role: PermissionRole,
        revision: int,
        recipients: tuple[sl.entity.EntityRef, ...],
        event: sd.SubmitEvent[RedstoneSquid],
    ) -> None:
        guild = event.context.guild
        assert guild is not None and guild.id == self._guild_id
        values: dict[tuple[int | None, int | None], AssignmentValues] = {}
        present = event.values["operation"] == "assign"
        for ref in recipients:
            member_id = int(ref.id)
            role_id = None
            account_id = None
            if ref.kind is sl.entity.EntityKind.ROLE:
                if guild.get_role(member_id) is None:
                    await event.notice("A selected role was deleted. Update the recipients and try again.")
                    return
                role_id = member_id
            else:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.NotFound:
                    await event.notice("A selected member left the server. Update the recipients and try again.")
                    return
                account_id = await get_or_create_discord_account_id(event.context.client.services.accounts, member)
            values[account_id, role_id] = AssignmentValues(
                role_id=role.id,
                present=present,
                account_id=account_id,
                discord_role_id=role_id,
                guild_id=guild.id,
                scope_guild_id=_scope(str(event.values.get("scope", "guild")), guild.id),
                reason=str(event.values.get("reason") or "") or None,
            )
        label = f"{'Assigned' if present else 'Removed'} {role.name} for {len(values)} recipients."
        await self._save(BULK_ASSIGNMENT_EDIT, AssignmentBatch(assignments=tuple(values.values())), revision, label)
        self._selected_role = (role, self._revision)
        self._recipients = ()
        await self._refresh()
        await event.notice(label)

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
    async def _rank_selected(self, role_id: int, revision: int, event: sd.SubmitEvent[RedstoneSquid]) -> None:
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
                    (
                        role
                        for role in await self._admin.roles(
                            context=ActingContext.for_actor(await self._actor()),
                            filters=GuildListFilters.for_guild(self._guild_id),
                        )
                        if str(role.id) == item.value
                    ),
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

    async def _delete_selected(self, role: PermissionRole, revision: int, _event: sd.PressEvent[RedstoneSquid]) -> None:
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
        rank = self._rank
        form = replace(
            form,
            items=tuple(
                replace(field, description=tr(t"access-rank-limit = Your management rank is {rank}. Use a lower rank."))
                if isinstance(field, sl.forms.IntField) and field.key == "rank"
                else field
                for field in form.items
            ),
        )
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
            fetch_size=8,
            title=title,
            empty=tr(t"access-permission-catalogue-empty = This role grants no actions."),
        )

    async def _account_names(self, ids: set[int]) -> dict[int, str]:
        if not self._bot or not ids:
            return {}
        accounts = await self._bot.services.accounts.get_accounts(tuple(ids))
        guild = self._bot.get_guild(self._guild_id)
        names = {}
        for key, account in accounts.items():
            identity = account.most_recent_identity_for(IdentityProvider.DISCORD)
            member = guild.get_member(int(identity.subject)) if guild and identity else None
            names[key] = (
                member.display_name
                if member
                else next(
                    (i.display_name for i in account.identities if i.display_name),
                    f"<@{identity.subject}>" if identity else "Unlinked account",
                )
            )
        return names

    async def _audit(
        self,
        rows: tuple[PermissionAuditEntry, ...],
    ) -> sp.Browser[PermissionAuditEntry, ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        accounts = await self._account_names(
            {
                identity
                for row in rows
                for identity in (row.actor_account_id, row.subject_id if row.subject_kind == "account" else None)
                if identity is not None
            }
        )
        roles = {
            role.id: role.name
            for role in await self._admin.roles(
                context=ActingContext.for_actor(await self._actor()), filters=GuildListFilters.for_guild(self._guild_id)
            )
        }
        guild = self._bot.get_guild(self._guild_id) if self._bot else None

        def subject(row: PermissionAuditEntry) -> str:
            if row.subject_kind == "discord_role" and row.subject_id is not None:
                role = guild.get_role(row.subject_id) if guild else None
                return role.name if role else "deleted Discord role"
            return accounts.get(row.subject_id or 0, "unknown member")

        def action(row: PermissionAuditEntry) -> str:
            details = row.details or {}
            role = roles.get(row.role_id or 0) or str(details.get("slug") or "deleted bot role")
            match row.action:
                case "role_assign":
                    return f"Assigned {role} to {subject(row)}"
                case "role_unassign":
                    return f"Removed {role} from {subject(row)}"
                case "grant":
                    return f"{effect_label(row.effect) if row.effect is not None else 'Grant'} {row.pattern} for {subject(row)}"
                case "revoke":
                    return f"Removed {row.pattern} rule for {subject(row)}"
                case "role_rank":
                    return f"Set {role} to rank {details.get('rank', 'unknown')}"
                case "role_compose":
                    return f"Added {details.get('included', 'a bot role')} to {role}"
                case "role_decompose":
                    return f"Removed {details.get('included', 'a bot role')} from {role}"
                case "role_create":
                    return f"Created {role}"
                case "role_withdraw" | "role_delete":
                    return f"Removed {role}"
                case "role_include" | "role_exclude" | "role_pattern_remove":
                    verb = {"role_include": "Included", "role_exclude": "Excluded", "role_pattern_remove": "Cleared"}[
                        row.action
                    ]
                    return f"{verb} {row.pattern} on {role}"
                case _:
                    return f"{row.action.removeprefix('role_').replace('_', ' ').capitalize()} {role} {row.pattern or ''}".rstrip()

        def audit_actor(row: PermissionAuditEntry) -> str:
            return accounts.get(row.actor_account_id or 0, "Unknown account" if row.actor_account_id else "System")

        return sp.Browser(
            sl.sources.list_source(rows),
            key="permission-audit",
            identity=lambda row: str(row.id),
            label=lambda row: action(row)[:100],
            summary=lambda row: f"{action(row)} · {audit_actor(row)} · <t:{row.at.timestamp()}:d>",
            detail=lambda row: sl.fields(
                sl.field("Change", action(row)),
                sl.field("Changed by", audit_actor(row)),
                sl.field("When", f"<t:{row.at.timestamp()}:f>"),
                sl.field("Reason", row.reason or "None recorded"),
                sl.field(
                    "Scope",
                    self._guild_name
                    if row.scope_guild_id or (not row.subject_kind and row.subject_guild_id)
                    else "All servers",
                ),
            ),
            fetch_size=8,
            title=tr(t"access-view-permission-audit = Audit log"),
            empty=tr(t"access-audit-empty = No permission changes are recorded for this server."),
        )

    async def _save(self, kind: str, values: PermissionValues, revision: int | None, label: sl.TextLike) -> None:
        await self.edits.bind(kind, PERMISSION_GRAPH, self._revision if revision is None else revision).save(
            values.edit_values(), label
        )

    @requires_grant(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL, denied=_DENIED)
    async def _change_rule[ContextT](
        self,
        event: sl.SubmitEvent[ContextT],
        *,
        target: AccessTarget,
        pattern: str | None = None,
    ) -> None:
        effect = {"allow": Effect.ALLOW, "deny": Effect.DENY, "forbid": Effect.FORBID}.get(
            str(event.values["operation"])
        )
        values = GrantValues(
            pattern=pattern or str(event.values["pattern"]),
            effect=effect,
            account_id=target.account_id,
            discord_role_id=target.discord_role_id,
            guild_id=self._guild_id,
            scope_guild_id=target.scope_guild_id,
            reason=str(event.values.get("reason") or "") or None,
        )
        self._review = AccessChange(
            values,
            target.revision,
            target.subject,
            target.member_id,
            pattern or str(event.values["pattern"]),
            str(event.values["operation"]),
        )

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _change_assignment[ContextT](
        self,
        event: sl.SubmitEvent[ContextT],
        *,
        target: AccessTarget,
        role: PermissionRole | None = None,
    ) -> None:
        if role is None:
            role = await self._admin.role(
                str(event.values["slug"]), context=ActingContext.for_actor(await self._actor()), guild_id=self._guild_id
            )
        values = AssignmentValues(
            role_id=role.id,
            present=event.values["operation"] == "assign",
            account_id=target.account_id,
            discord_role_id=target.discord_role_id,
            guild_id=self._guild_id,
            scope_guild_id=target.scope_guild_id,
            reason=str(event.values.get("reason") or "") or None,
        )
        self._review = AccessChange(
            values,
            target.revision,
            target.subject,
            target.member_id,
            role.name,
            str(event.values["operation"]),
        )

    @requires_grant(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, denied=_DENIED)
    async def _create_role[ContextT](
        self, event: sl.SubmitEvent[ContextT], *, base_revision: int | None = None
    ) -> None:
        form = _CreateRoleForm.from_submission(event)
        scope = str(event.values.get("scope") or ("global" if ROLE_DEFINITION_MANAGE in self.grants else "guild"))
        slug = form.slug
        if not slug:
            stem = re.sub(r"[^a-z0-9]+", "-", form.name.lower()).strip("-")[:28]
            if not stem or not stem[0].isalpha():
                stem = f"role-{stem}"[:28]
            if len(stem) < 2:
                stem += "-role"
            existing = {
                role.slug
                for role in await self._admin.roles(
                    context=ActingContext.for_actor(await self._actor()),
                    filters=GuildListFilters.for_guild(_scope(scope, self._guild_id)),
                )
            }
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
                role_id=await self._admin.reserve_role_identity(context=ActingContext.for_actor(await self._actor())),
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


def _subject_item_summary(row: PermissionGrant | PermissionRoleAssignment, scope: str) -> str:
    effect = effect_label(row.effect) if isinstance(row, PermissionGrant) else "Assigned bot role"
    return f"{_subject_item_label(row)} · {effect} · {scope}"


def _subject_item_detail(row: PermissionGrant | PermissionRoleAssignment, scope: str) -> sl.semantic.Fields:
    if isinstance(row, PermissionGrant):
        return sl.fields(
            sl.field(tr(t"access-view-effect = Effect"), effect_label(row.effect)),
            sl.field(tr(t"access-view-pattern = Pattern"), row.pattern),
            sl.field(
                tr(t"access-view-scope = Where this applies"),
                scope,
            ),
            sl.field(tr(t"access-view-reason = Reason"), row.reason or "—"),
        )
    return sl.fields(
        sl.field(tr(t"access-view-internal-role = Internal role"), row.role_slug),
        sl.field(
            tr(t"access-view-scope = Where this applies"),
            scope,
        ),
    )


def _role_summary(role: PermissionRole) -> str:
    scope = "global" if role.guild_id is None else "this server"
    protected = " · built-in" if role.protected else ""
    description = f" — {role.description}" if role.description else ""
    return f"{role.name}{description} · rank {role.rank} · {scope}{protected}"


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
        description=tr(
            t"access-view-rank-help = Higher ranks can manage lower ranks. Rank controls who can manage this role."
        ),
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
