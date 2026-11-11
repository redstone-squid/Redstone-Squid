"""Permission administration and delegation checks.

An editor's capabilities limit what they can grant, and their role rank limits which roles
they can manage.
"""

import dataclasses
from functools import partial

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from whenever import Instant

from squid.core.actors import Actor
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
from squid.permissions.application.ports import AuditEntry, SubjectRecords
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Effect
from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork

from ..domain.strategies import patterns as domain_patterns

GUILD = 555


class FakeStore:
    """State for administration checks, shared by the read and write ports.

    Actor loads filter account grants and assignments; Discord role subjects are
    not modeled. Role lookups ignore guild scope. Some removal methods only record
    the audit call, so their storage behavior needs integration coverage.
    """

    def __init__(self, *, roles: tuple[PermissionRole, ...] = (), rules: tuple[PermissionGrant, ...] = ()) -> None:
        builtin = tuple(
            PermissionRole(
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
        self.assignments: list[PermissionRoleAssignment] = []
        self.audit_entries: list[AuditEntry] = []
        self._next_id = 100

    # read side ---------------------------------------------------------
    async def load_for_subject(self, *, account_id, discord_role_ids, guild_id) -> SubjectRecords:
        return SubjectRecords(
            epoch=1,
            roles=tuple(self.roles.values()),
            grants=tuple(
                PermissionGrant(
                    pattern=rule.pattern,
                    effect=rule.effect,
                    subject_account_id=rule.subject_account_id,
                    scope_guild_id=rule.scope_guild_id,
                )
                for rule in self.rules
                if rule.subject_account_id == account_id
            ),
            assignments=tuple(
                PermissionRoleAssignment(
                    role_id=assignment.role_id,
                    subject_account_id=assignment.subject_account_id,
                    scope_guild_id=assignment.scope_guild_id,
                )
                for assignment in self.assignments
                if assignment.subject_account_id == account_id
            ),
        )

    async def epoch(self) -> int:
        return 1

    # write side --------------------------------------------------------
    async def upsert_grant(self, *, audit: AuditEntry, **kwargs) -> None:
        self._next_id += 1
        self.rules.append(
            PermissionGrant(
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

    async def list_rules(self, **_kwargs: object) -> tuple[PermissionGrant, ...]:
        return tuple(self.rules)

    async def list_assignments(self, **_kwargs: object) -> tuple[PermissionRoleAssignment, ...]:
        return tuple(self.assignments)

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[PermissionRole, ...]:
        return tuple(self.roles.values())

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> PermissionRole | None:
        return next((role for role in self.roles.values() if role.slug == slug), None)

    async def create_role(
        self, *, slug, name, description, guild_id, rank, created_by_account_id, audit
    ) -> PermissionRole:
        self._next_id += 1
        role = PermissionRole(
            id=self._next_id, slug=slug, guild_id=guild_id, builtin_key=None, rank=rank, protected=False
        )
        self.roles[role.id] = role
        self.audit_entries.append(audit)
        return role

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool:
        self.audit_entries.append(audit)
        return self.roles.pop(role_id, None) is not None

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None:
        role = self.roles[role_id]
        self.roles[role_id] = PermissionRole(
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
        self.roles[role_id] = PermissionRole(
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
        self.roles[role_id] = PermissionRole(
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
            PermissionRoleAssignment(
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

    async def list_audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[PermissionAuditEntry, ...]:
        return ()


class FakePermissionUnitOfWork(FakeUnitOfWork[FakeStore]):
    @property
    def permissions(self) -> FakeStore:
        return self.state

    @property
    def permission_admin(self) -> FakeStore:
        return self.state


def _copy_store(store: FakeStore) -> FakeStore:
    copied = FakeStore()
    copied.roles = {key: dataclasses.replace(role) for key, role in store.roles.items()}
    copied.rules = [dataclasses.replace(rule) for rule in store.rules]
    copied.assignments = [dataclasses.replace(assignment) for assignment in store.assignments]
    copied.audit_entries = list(store.audit_entries)
    copied._next_id = store._next_id
    return copied


def stored(
    *, roles: tuple[PermissionRole, ...] = (), rules: tuple[PermissionGrant, ...] = ()
) -> CommittedState[FakeStore]:
    return CommittedState(FakeStore(roles=roles, rules=rules), clone=_copy_store)


def admin(store: CommittedState[FakeStore]) -> PermissionAdministrationService:
    factory = partial(FakePermissionUnitOfWork, store)
    return PermissionAdministrationService(PermissionService(uow_factory=factory), uow_factory=factory)


def granted(*patterns: str, account_id: int = 7) -> tuple[PermissionGrant, ...]:
    return tuple(
        PermissionGrant(id=index + 1, pattern=pattern, effect=int(Effect.ALLOW), subject_account_id=account_id)
        for index, pattern in enumerate(patterns)
    )


class TestAuthorityBoundary:
    async def test_a_held_pattern_can_be_granted(self) -> None:
        store = stored(rules=granted("perm.grant.global", "build.**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        await service.grant(actor, pattern="build.submission.approve", account_id=8, scope_guild_id=None)

        assert any(rule.subject_account_id == 8 for rule in store.value.rules)

    async def test_a_pattern_beyond_the_actor_is_refused(self) -> None:
        store = stored(rules=granted("perm.grant.global", "build.**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="outside your authority"):
            await service.grant(actor, pattern="bot.tree.sync", account_id=8, scope_guild_id=None)

    async def test_the_owner_bypasses_every_gate(self) -> None:
        store = stored()
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=1, is_bot_owner=True))

        await service.grant(actor, pattern="**", account_id=8, scope_guild_id=None)

        assert store.value.rules


class TestGuildDelegation:
    async def test_a_guild_granter_cannot_reach_a_global_node(self) -> None:
        """Check the administration service refuses a global capability in a guild-scoped grant."""
        store = stored(rules=granted("perm.grant.guild", "settings.**", "build.**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="not this server's to grant"):
            await service.grant(actor, pattern="build.**", account_id=8, scope_guild_id=GUILD)

    async def test_a_guild_granter_cannot_write_a_global_rule(self) -> None:
        store = stored(rules=granted("perm.grant.guild", "settings.**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionAuthorityError, match="scoped to this server"):
            await service.grant(actor, pattern="settings.server.edit", account_id=8, scope_guild_id=None)

    async def test_a_guild_scoped_guild_node_is_allowed(self) -> None:
        store = stored(rules=granted("perm.grant.guild", "settings.**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        await service.grant(actor, pattern="settings.server.edit", account_id=8, scope_guild_id=GUILD)

        assert store.value.rules[-1].scope_guild_id == GUILD


class TestForbid:
    async def test_forbid_is_owner_only(self) -> None:
        store = stored(rules=granted("**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7))

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
        store = stored()
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=1, is_bot_owner=True))

        with pytest.raises(ValidationError):
            await service.grant(actor, pattern="build.**", effect=Effect.FORBID, account_id=8, scope_guild_id=None)


class TestRankGate:
    async def test_a_peer_role_cannot_be_managed(self) -> None:
        """Holding the management permission does not allow editing a role at the actor's own rank."""
        store = stored(
            roles=(
                PermissionRole(id=50, slug="moderator", guild_id=GUILD, builtin_key=None, rank=60, protected=False),
            ),
            rules=granted("**", "role.definition.manage_guild"),
        )
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))
        store.value.assignments.append(
            PermissionRoleAssignment(
                id=1, role_id=50, role_slug="moderator", subject_account_id=7, scope_guild_id=GUILD
            )
        )
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        with pytest.raises(PermissionRankError, match="at or above"):
            await service.set_rank(actor, await service.role("moderator"), 10)

    async def test_a_builtin_refuses_structural_edits(self) -> None:
        store = stored(rules=granted("**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        with pytest.raises(ProtectedRoleError):
            await service.add_pattern(actor, await service.role("global-admin"), "bot.**")


class TestRoles:
    async def test_a_role_resolves_to_its_leaves(self) -> None:
        store = stored()
        service = admin(store)
        roles = await service.roles()
        trusted = await service.role("trusted")

        assert "vote.log_delete.cast" in service.resolved_leaves(trusted, roles)
        assert "bot.tree.sync" not in service.resolved_leaves(await service.role("global-admin"), roles)

    async def test_composition_cycles_are_refused_at_write_time(self) -> None:
        store = stored(
            roles=(
                PermissionRole(
                    id=50, slug="a", guild_id=None, builtin_key=None, rank=10, protected=False, includes_roles=(51,)
                ),
                PermissionRole(id=51, slug="b", guild_id=None, builtin_key=None, rank=10, protected=False),
            ),
            rules=granted("**", "role.definition.manage"),
        )
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=1, is_bot_owner=True))

        with pytest.raises(ConflictError, match="cycle"):
            await service.add_include(actor, await service.role("b"), await service.role("a"))

    @pytest.mark.parametrize(("role_guild", "included_guild"), [(None, GUILD), (GUILD, GUILD + 1)])
    async def test_a_role_cannot_include_another_guilds_role(self, role_guild: int | None, included_guild: int) -> None:
        """A guild role inside a global role would let one guild change what that role grants everywhere."""
        store = stored(
            roles=(
                PermissionRole(id=50, slug="outer", guild_id=role_guild, builtin_key=None, rank=10, protected=False),
                PermissionRole(
                    id=51, slug="inner", guild_id=included_guild, builtin_key=None, rank=10, protected=False
                ),
            ),
            rules=granted("**", "role.definition.manage"),
        )
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=1, is_bot_owner=True))

        with pytest.raises(ValidationError, match="own guild"):
            await service.add_include(actor, await service.role("outer"), await service.role("inner"))

    async def test_an_exclusion_needs_no_authority_over_what_it_removes(self) -> None:
        """An exclusion narrows a role, so the editor need not hold the removed capability."""
        store = stored(
            roles=(
                PermissionRole(id=50, slug="helper", guild_id=None, builtin_key=None, rank=5, protected=False),
                PermissionRole(id=51, slug="lead", guild_id=None, builtin_key=None, rank=40, protected=False),
            ),
            rules=granted("role.definition.manage", "settings.**"),
        )
        store.value.assignments.append(
            PermissionRoleAssignment(id=1, role_id=51, role_slug="lead", subject_account_id=7)
        )
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

        await service.add_pattern(actor, await service.role("helper"), "bot.**", mode=EXCLUDE_MODE)

        assert "bot.**" in (await service.role("helper")).excludes

    async def test_show_surfaces_patterns_the_actor_cannot_manage(self) -> None:
        store = stored(
            roles=(
                PermissionRole(
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
        actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))
        roles = await service.roles()

        assert service.unmanageable_patterns(actor, await service.role("helper"), roles) == ("bot.tree.sync",)


class TestLastGranter:
    async def test_the_last_global_granter_cannot_be_revoked(self) -> None:
        """Preserve a stored holder of global grant authority."""
        store = stored(rules=granted("perm.grant.global", "**"))
        service = admin(store)
        actor = await service.resolve_actor(Actor(account_id=7))

        with pytest.raises(ConflictError, match="last holder"):
            await service.revoke(actor, pattern="perm.grant.global", account_id=7)


async def test_every_mutation_records_an_audit_entry() -> None:
    store = stored(rules=granted("**"))
    service = admin(store)
    actor = await service.resolve_actor(Actor(account_id=7, guild_id=GUILD))

    await service.grant(
        actor,
        pattern="settings.server.edit",
        account_id=8,
        scope_guild_id=None,
        expires_at=Instant.from_utc(2030, 1, 1),
        reason="event weekend",
    )

    assert [entry.action for entry in store.value.audit_entries] == ["grant"]
    assert store.value.audit_entries[0].reason == "event weekend"


# P11 checks sequences as well as individual edits: composition can expose a capability
# indirectly through another role, so each accepted step must preserve the authority bound.

CUSTOM_A = "custom-role-a"
CUSTOM_B = "custom-role-b"
SEED_ROLE = "seed-assigned"
"""Give the actor a starting rank independently of the two roles being edited."""

REJECTIONS = (PermissionAuthorityError, PermissionRankError, ProtectedRoleError, ValidationError, ConflictError)


@dataclasses.dataclass(frozen=True, slots=True)
class _IncludeOp:
    slug: str
    pattern: str
    mode: int = INCLUDE_MODE


@dataclasses.dataclass(frozen=True, slots=True)
class _ComposeOp:
    slug: str
    included_slug: str


@dataclasses.dataclass(frozen=True, slots=True)
class _AssignOp:
    slug: str


@dataclasses.dataclass(frozen=True, slots=True)
class _RankOp:
    slug: str
    rank: int


_P11Op = _IncludeOp | _ComposeOp | _AssignOp | _RankOp


@dataclasses.dataclass(frozen=True, slots=True)
class _P11Scenario:
    initial_patterns: list[str]
    actor_rank: int
    rank_a: int
    rank_b: int
    guild_a: int | None
    guild_b: int | None
    ops: list[_P11Op]


def _p11_operations() -> st.SearchStrategy[_P11Op]:
    slugs = st.sampled_from((CUSTOM_A, CUSTOM_B))
    compose_targets = st.sampled_from((CUSTOM_A, CUSTOM_B, "trusted", "guild-admin", "global-admin", "owner"))
    return st.one_of(
        st.builds(_IncludeOp, slug=slugs, pattern=domain_patterns(), mode=st.just(INCLUDE_MODE)),
        st.builds(_IncludeOp, slug=slugs, pattern=domain_patterns(), mode=st.just(EXCLUDE_MODE)),
        st.builds(_ComposeOp, slug=slugs, included_slug=compose_targets),
        st.builds(_AssignOp, slug=slugs),
        st.builds(_RankOp, slug=slugs, rank=st.integers(min_value=0, max_value=999)),
    )


@st.composite
def _p11_scenarios(draw: st.DrawFn) -> _P11Scenario:
    return _P11Scenario(
        initial_patterns=draw(st.lists(domain_patterns(), max_size=5, unique=True)),
        actor_rank=draw(st.integers(min_value=0, max_value=900)),
        rank_a=draw(st.integers(min_value=0, max_value=999)),
        rank_b=draw(st.integers(min_value=0, max_value=999)),
        guild_a=draw(st.sampled_from((None, GUILD))),
        guild_b=draw(st.sampled_from((None, GUILD))),
        ops=draw(st.lists(_p11_operations(), max_size=6)),
    )


async def _p11_apply(service: PermissionAdministrationService, actor: Actor, op: _P11Op) -> None:
    if isinstance(op, _IncludeOp):
        await service.add_pattern(actor, await service.role(op.slug), op.pattern, mode=op.mode)
    elif isinstance(op, _ComposeOp):
        await service.add_include(actor, await service.role(op.slug), await service.role(op.included_slug))
    elif isinstance(op, _AssignOp):
        role = await service.role(op.slug)
        await service.assign(actor, role, account_id=7, scope_guild_id=role.guild_id)
    elif isinstance(op, _RankOp):
        await service.set_rank(actor, await service.role(op.slug), op.rank)


@settings(max_examples=200, deadline=None)
@given(scenario=_p11_scenarios())
async def test_p11_accepted_role_edits_never_grow_the_editors_authority(scenario: _P11Scenario) -> None:
    """P11: generated edit sequences must not expand the editor's authority.

    After each accepted edit, compare the editor's capabilities with both the previous
    step and the initial set. At the end, both edited roles must also stay within
    that initial set, so assigning either role to someone else cannot grant more.
    """
    store = stored(
        roles=(
            PermissionRole(
                id=200,
                slug=CUSTOM_A,
                guild_id=scenario.guild_a,
                builtin_key=None,
                rank=scenario.rank_a,
                protected=False,
            ),
            PermissionRole(
                id=201,
                slug=CUSTOM_B,
                guild_id=scenario.guild_b,
                builtin_key=None,
                rank=scenario.rank_b,
                protected=False,
            ),
            PermissionRole(
                id=202,
                slug=SEED_ROLE,
                guild_id=None,
                builtin_key=None,
                rank=scenario.actor_rank,
                protected=False,
            ),
        ),
        rules=granted(*scenario.initial_patterns),
    )
    store.value.assignments.append(
        PermissionRoleAssignment(id=1, role_id=202, role_slug=SEED_ROLE, subject_account_id=7)
    )
    service = admin(store)
    subject = Actor(account_id=7, guild_id=GUILD)
    initial_held = (await service.resolve_actor(subject)).capabilities

    for op in scenario.ops:
        actor = await service.resolve_actor(subject)
        before_held = actor.capabilities
        try:
            await _p11_apply(service, actor, op)
        except REJECTIONS:
            continue
        after_held = (await service.resolve_actor(subject)).capabilities
        assert after_held <= before_held
        assert after_held <= initial_held

    roles = await service.roles()
    for slug in (CUSTOM_A, CUSTOM_B):
        leaves = frozenset(service.resolved_leaves(await service.role(slug), roles))
        assert leaves <= initial_held
