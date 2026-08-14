"""The two management gates, and the delegation rules behind `/perm`."""

import pytest
from whenever import Instant

from squid.core.errors import ConflictError, ValidationError
from squid.permissions.application import PermissionService
from squid.permissions.application.administration import (
    EXCLUDE_MODE,
    INCLUDE_MODE,
    PermissionAdministrationService,
    PermissionAuthorityError,
    PermissionRankError,
    ProtectedRoleError,
)
from squid.permissions.application.ports import (
    AssignmentRow,
    AuditEntry,
    AuditRow,
    GrantRecord,
    RoleRecord,
    RuleRow,
    SubjectRecords,
)
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Effect, Subject

GUILD = 555


class FakeStore:
    """One in-memory store standing in for both halves of the repository."""

    def __init__(self, *, roles: tuple[RoleRecord, ...] = (), rules: tuple[RuleRow, ...] = ()) -> None:
        builtin = tuple(
            RoleRecord(
                id=index + 1,
                slug=role.key,
                guild_id=None,
                builtin_key=role.key,
                rank=role.rank,
                protected=True,
            )
            for index, role in enumerate(BUILTIN_ROLES_BY_KEY.values())
        )
        self.roles = {role.id: role for role in (*builtin, *roles)}
        self.rules = list(rules)
        self.assignments: list[AssignmentRow] = []
        self.audit_entries: list[AuditEntry] = []
        self._next_id = 100

    # read side ---------------------------------------------------------
    async def load_for_subject(self, *, account_id, discord_role_ids, guild_id) -> SubjectRecords:
        return SubjectRecords(
            epoch=1,
            roles=tuple(self.roles.values()),
            grants=tuple(
                GrantRecord(
                    pattern=rule.pattern,
                    effect=rule.effect,
                    subject_account_id=rule.subject_account_id,
                    scope_guild_id=rule.scope_guild_id,
                )
                for rule in self.rules
                if rule.subject_account_id == account_id
            ),
        )

    async def epoch(self) -> int:
        return 1

    # write side --------------------------------------------------------
    async def upsert_grant(self, *, audit: AuditEntry, **kwargs) -> None:
        self._next_id += 1
        self.rules.append(
            RuleRow(
                id=self._next_id,
                pattern=kwargs["pattern"],
                effect=kwargs["effect"],
                subject_account_id=kwargs["subject_account_id"],
                subject_role_id=kwargs["subject_role_id"],
                scope_guild_id=kwargs["scope_guild_id"],
                reason=kwargs["reason"],
            )
        )
        self.audit_entries.append(audit)

    async def delete_grant(self, *, pattern, subject_account_id, subject_role_id, scope_guild_id, audit) -> bool:
        before = len(self.rules)
        self.rules = [
            rule
            for rule in self.rules
            if not (rule.pattern == pattern and rule.subject_account_id == subject_account_id)
        ]
        self.audit_entries.append(audit)
        return len(self.rules) != before

    async def list_rules(self, **_kwargs: object) -> tuple[RuleRow, ...]:
        return tuple(self.rules)

    async def list_assignments(self, **_kwargs: object) -> tuple[AssignmentRow, ...]:
        return tuple(self.assignments)

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[RoleRecord, ...]:
        return tuple(self.roles.values())

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> RoleRecord | None:
        return next((role for role in self.roles.values() if role.slug == slug), None)

    async def create_role(self, *, slug, name, description, guild_id, rank, created_by_account_id, audit) -> RoleRecord:
        self._next_id += 1
        role = RoleRecord(id=self._next_id, slug=slug, guild_id=guild_id, builtin_key=None, rank=rank, protected=False)
        self.roles[role.id] = role
        self.audit_entries.append(audit)
        return role

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool:
        self.audit_entries.append(audit)
        return self.roles.pop(role_id, None) is not None

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None:
        role = self.roles[role_id]
        self.roles[role_id] = RoleRecord(
            id=role.id,
            slug=role.slug,
            guild_id=role.guild_id,
            builtin_key=role.builtin_key,
            rank=rank,
            protected=role.protected,
            includes=role.includes,
            excludes=role.excludes,
            includes_roles=role.includes_roles,
        )
        self.audit_entries.append(audit)

    async def set_role_pattern(self, role_id, pattern, mode, *, added_by_account_id, audit) -> None:
        role = self.roles[role_id]
        includes = (*role.includes, pattern) if mode == INCLUDE_MODE else role.includes
        excludes = (*role.excludes, pattern) if mode == EXCLUDE_MODE else role.excludes
        self.roles[role_id] = RoleRecord(
            id=role.id,
            slug=role.slug,
            guild_id=role.guild_id,
            builtin_key=role.builtin_key,
            rank=role.rank,
            protected=role.protected,
            includes=includes,
            excludes=excludes,
            includes_roles=role.includes_roles,
        )
        self.audit_entries.append(audit)

    async def remove_role_pattern(self, role_id: int, pattern: str, *, audit: AuditEntry) -> bool:
        self.audit_entries.append(audit)
        return True

    async def add_role_include(self, role_id, included_role_id, *, added_by_account_id, audit) -> None:
        role = self.roles[role_id]
        self.roles[role_id] = RoleRecord(
            id=role.id,
            slug=role.slug,
            guild_id=role.guild_id,
            builtin_key=role.builtin_key,
            rank=role.rank,
            protected=role.protected,
            includes=role.includes,
            excludes=role.excludes,
            includes_roles=(*role.includes_roles, included_role_id),
        )
        self.audit_entries.append(audit)

    async def remove_role_include(self, role_id: int, included_role_id: int, *, audit: AuditEntry) -> bool:
        self.audit_entries.append(audit)
        return True

    async def assign_role(self, role_id, *, audit, **kwargs) -> None:
        self._next_id += 1
        self.assignments.append(
            AssignmentRow(
                id=self._next_id,
                role_id=role_id,
                role_slug=self.roles[role_id].slug,
                subject_account_id=kwargs["subject_account_id"],
                subject_role_id=kwargs["subject_role_id"],
                scope_guild_id=kwargs["scope_guild_id"],
            )
        )
        self.audit_entries.append(audit)

    async def unassign_role(self, role_id, *, subject_account_id, subject_role_id, scope_guild_id, audit) -> bool:
        self.audit_entries.append(audit)
        return True

    async def list_audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[AuditRow, ...]:
        return ()


def admin(store: FakeStore) -> PermissionAdministrationService:
    return PermissionAdministrationService(store, PermissionService(store))


def granted(*patterns: str, account_id: int = 7) -> tuple[RuleRow, ...]:
    return tuple(
        RuleRow(id=index + 1, pattern=pattern, effect=int(Effect.ALLOW), subject_account_id=account_id)
        for index, pattern in enumerate(patterns)
    )


class TestAuthorityBoundary:
    async def test_a_held_pattern_can_be_granted(self) -> None:
        store = FakeStore(rules=granted("perm.grant.global", "build.**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        await service.grant(actor, pattern="build.submission.approve", account_id=8, scope_guild_id=None)

        assert any(rule.subject_account_id == 8 for rule in store.rules)

    async def test_a_pattern_beyond_the_actor_is_refused(self) -> None:
        """You cannot mint yourself, or anyone else, something you lack."""
        store = FakeStore(rules=granted("perm.grant.global", "build.**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="outside your authority"):
            await service.grant(actor, pattern="bot.tree.sync", account_id=8, scope_guild_id=None)

    async def test_the_owner_bypasses_every_gate(self) -> None:
        store = FakeStore()
        service = admin(store)
        actor = await service.actor(Subject(account_id=1, is_bot_owner=True))

        await service.grant(actor, pattern="**", account_id=8, scope_guild_id=None)

        assert store.rules


class TestGuildDelegation:
    async def test_a_guild_granter_cannot_reach_a_global_node(self) -> None:
        """Blocked here, at the resolver, and by a database CHECK."""
        store = FakeStore(rules=granted("perm.grant.guild", "settings.**", "build.**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="not this server's to grant"):
            await service.grant(actor, pattern="build.**", account_id=8, scope_guild_id=GUILD)

    async def test_a_guild_granter_cannot_write_a_global_rule(self) -> None:
        store = FakeStore(rules=granted("perm.grant.guild", "settings.**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="scoped to this server"):
            await service.grant(actor, pattern="settings.server.edit", account_id=8, scope_guild_id=None)

    async def test_a_guild_scoped_guild_node_is_allowed(self) -> None:
        store = FakeStore(rules=granted("perm.grant.guild", "settings.**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        await service.grant(actor, pattern="settings.server.edit", account_id=8, scope_guild_id=GUILD)

        assert store.rules[-1].scope_guild_id == GUILD


class TestForbid:
    async def test_forbid_is_owner_only(self) -> None:
        store = FakeStore(rules=granted("**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7))

        with pytest.raises(PermissionAuthorityError, match="owner"):
            await service.grant(
                actor,
                pattern="build.**",
                effect=Effect.FORBID,
                account_id=8,
                scope_guild_id=None,
                reason="abuse",
            )

    async def test_forbid_requires_a_reason(self) -> None:
        store = FakeStore()
        service = admin(store)
        actor = await service.actor(Subject(account_id=1, is_bot_owner=True))

        with pytest.raises(ValidationError):
            await service.grant(actor, pattern="build.**", effect=Effect.FORBID, account_id=8, scope_guild_id=None)


class TestRankGate:
    async def test_a_peer_role_cannot_be_managed(self) -> None:
        """The boundary alone permits lateral sabotage; rank is what stops it."""
        store = FakeStore(
            roles=(RoleRecord(id=50, slug="moderator", guild_id=GUILD, builtin_key=None, rank=60, protected=False),),
            rules=granted("**", "role.definition.manage_guild"),
        )
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))
        store.assignments.append(
            AssignmentRow(id=1, role_id=50, role_slug="moderator", subject_account_id=7, scope_guild_id=GUILD)
        )
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionRankError, match="at or above"):
            await service.set_rank(actor, await service.role("moderator"), 10)

    async def test_a_builtin_refuses_structural_edits(self) -> None:
        store = FakeStore(rules=granted("**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        with pytest.raises(ProtectedRoleError):
            await service.add_pattern(actor, await service.role("global-admin"), "bot.**")


class TestRoles:
    async def test_a_role_resolves_to_its_leaves(self) -> None:
        store = FakeStore()
        service = admin(store)
        roles = await service.roles()
        trusted = await service.role("trusted")

        assert "vote.log_delete.cast" in service.resolved_leaves(trusted, roles)
        assert "bot.tree.sync" not in service.resolved_leaves(await service.role("global-admin"), roles)

    async def test_composition_cycles_are_refused_at_write_time(self) -> None:
        store = FakeStore(
            roles=(
                RoleRecord(
                    id=50, slug="a", guild_id=None, builtin_key=None, rank=10, protected=False, includes_roles=(51,)
                ),
                RoleRecord(id=51, slug="b", guild_id=None, builtin_key=None, rank=10, protected=False),
            ),
            rules=granted("**", "role.definition.manage"),
        )
        service = admin(store)
        actor = await service.actor(Subject(account_id=1, is_bot_owner=True))

        with pytest.raises(ConflictError, match="cycle"):
            await service.add_include(actor, await service.role("b"), await service.role("a"))

    async def test_an_exclusion_needs_no_authority_over_what_it_removes(self) -> None:
        """Subtracting can only narrow a role, so requiring the node would stop an
        administrator from removing a capability they cannot themselves hold."""
        store = FakeStore(
            roles=(
                RoleRecord(id=50, slug="helper", guild_id=None, builtin_key=None, rank=5, protected=False),
                RoleRecord(id=51, slug="lead", guild_id=None, builtin_key=None, rank=40, protected=False),
            ),
            rules=granted("role.definition.manage", "settings.**"),
        )
        store.assignments.append(AssignmentRow(id=1, role_id=51, role_slug="lead", subject_account_id=7))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

        await service.add_pattern(actor, await service.role("helper"), "bot.**", mode=EXCLUDE_MODE)

        assert "bot.**" in (await service.role("helper")).excludes

    async def test_show_surfaces_patterns_the_actor_cannot_manage(self) -> None:
        store = FakeStore(
            roles=(
                RoleRecord(
                    id=50,
                    slug="helper",
                    guild_id=None,
                    builtin_key=None,
                    rank=5,
                    protected=False,
                    includes=("bot.tree.sync", "settings.server.edit"),
                ),
            ),
            rules=granted("settings.**", "role.definition.manage"),
        )
        service = admin(store)
        actor = await service.actor(Subject(account_id=7, guild_id=GUILD))
        roles = await service.roles()

        assert service.unmanageable_patterns(actor, await service.role("helper"), roles) == ("bot.tree.sync",)


class TestLastGranter:
    async def test_the_last_global_granter_cannot_be_revoked(self) -> None:
        """Owner lockout is the failure nobody can recover from in-product."""
        store = FakeStore(rules=granted("perm.grant.global", "**"))
        service = admin(store)
        actor = await service.actor(Subject(account_id=7))

        with pytest.raises(ConflictError, match="last holder"):
            await service.revoke(actor, pattern="perm.grant.global", account_id=7)


async def test_every_mutation_records_an_audit_entry() -> None:
    store = FakeStore(rules=granted("**"))
    service = admin(store)
    actor = await service.actor(Subject(account_id=7, guild_id=GUILD))

    await service.grant(
        actor,
        pattern="settings.server.edit",
        account_id=8,
        scope_guild_id=None,
        expires_at=Instant.from_utc(2030, 1, 1),
        reason="event weekend",
    )

    assert [entry.action for entry in store.audit_entries] == ["grant"]
    assert store.audit_entries[0].reason == "event weekend"
