"""PermissionService: assembling stored rows into resolver rules."""

from collections.abc import Iterable
from dataclasses import replace

import pytest

from squid.core.actors import Actor
from squid.permissions.application import PermissionService
from squid.permissions.application.cache import SubjectRuleCache
from squid.permissions.application.ports import SubjectRecords
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Effect, Origin, Reason
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.permissions.errors import CheckMode, PermissionDeniedError
from squid.permissions.models import PermissionGrant, PermissionRole, PermissionRoleAssignment
from tests.support.permissions import permission_uow_factory

GUILD = 555

BUILTIN_ROWS = tuple(
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
ROLE_ID = {row.builtin_key: row.id for row in BUILTIN_ROWS}


class FakeStore:
    """Return the supplied records for any subject and count load calls."""

    def __init__(self, records: SubjectRecords):
        self.records = records
        self.loads = 0

    async def load_for_subject(
        self,
        *,
        account_id: int | None,
        discord_role_ids: Iterable[int],
        guild_id: int | None,
    ) -> SubjectRecords:
        self.loads += 1
        return self.records

    async def epoch(self) -> int:
        return self.records.epoch


def service(
    *,
    roles: tuple[PermissionRole, ...] = BUILTIN_ROWS,
    grants: tuple[PermissionGrant, ...] = (),
    assignments: tuple[PermissionRoleAssignment, ...] = (),
) -> tuple[PermissionService, FakeStore]:
    store = FakeStore(SubjectRecords(epoch=1, roles=roles, grants=grants, assignments=assignments))
    return PermissionService(uow_factory=permission_uow_factory(store)), store


async def test_a_builtin_role_uses_the_pattern_list_defined_in_code() -> None:
    """The seeded rows carry no patterns; the catalogue module supplies them."""
    permissions, _ = service(
        assignments=(PermissionRoleAssignment(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
    )
    subject = Actor(account_id=7, guild_id=GUILD)

    assert await permissions.allows(subject, "build.submission.approve")
    assert not await permissions.allows(subject, "record.entry.rebuild")


def widened(key: str, *patterns: str) -> tuple[PermissionRole, ...]:
    """The built-in rows, with stored include patterns added to one of them."""
    return tuple(
        PermissionRole(
            id=row.id,
            slug=row.slug,
            guild_id=row.guild_id,
            builtin_key=row.builtin_key,
            rank=row.rank,
            protected=row.protected,
            includes=patterns if row.builtin_key == key else (),
        )
        for row in BUILTIN_ROWS
    )


async def test_stored_patterns_extend_a_builtin_rather_than_replacing_it() -> None:
    """An operator can widen a built-in live, without a deploy."""
    permissions, _ = service(
        roles=widened("guild-admin", "perm.audit.view"),
        assignments=(PermissionRoleAssignment(role_id=ROLE_ID["guild-admin"], subject_account_id=7),),
    )
    subject = Actor(account_id=7, guild_id=GUILD)

    assert await permissions.allows(subject, "perm.audit.view")
    assert await permissions.allows(subject, "settings.server.edit")


async def test_a_stored_pattern_cannot_punch_through_a_builtins_exclude() -> None:
    """Built-in exclusions still apply when stored include patterns overlap them."""
    permissions, _ = service(
        roles=widened("global-admin", "bot.**", "record.entry.rebuild"),
        assignments=(PermissionRoleAssignment(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
    )
    subject = Actor(account_id=7, guild_id=GUILD)

    assert not await permissions.allows(subject, "bot.tree.sync")
    assert not await permissions.allows(subject, "record.entry.rebuild")


async def test_a_role_assignment_to_a_discord_role_reaches_its_holders() -> None:
    permissions, _ = service(
        assignments=(PermissionRoleAssignment(role_id=ROLE_ID["trusted"], subject_role_id=99, subject_guild_id=GUILD),),
    )
    subject = Actor(account_id=7, discord_role_ids=frozenset({99}), guild_id=GUILD)

    assert await permissions.allows(subject, "vote.log_delete.cast")


class TestGuildAdminBridge:
    async def test_manage_server_confers_the_guild_nodes(self) -> None:
        permissions, _ = service()
        subject = Actor(account_id=7, guild_id=GUILD, discord_guild_admin=True)

        assert await permissions.allows(subject, "settings.server.edit")
        assert not await permissions.allows(subject, "build.submission.approve")

    async def test_it_is_absent_without_manage_server(self) -> None:
        permissions, _ = service()
        subject = Actor(account_id=7, guild_id=GUILD)

        assert not await permissions.allows(subject, "settings.server.edit")

    async def test_an_explicit_deny_outranks_it(self) -> None:
        """An explicit deny can override the grant derived from Discord's Manage Server permission."""
        permissions, _ = service(
            grants=(PermissionGrant(pattern="settings.server.edit", effect=int(Effect.DENY), subject_account_id=7),),
        )
        subject = Actor(account_id=7, guild_id=GUILD, discord_guild_admin=True)
        decision = await permissions.check(subject, "settings.server.edit")

        assert not decision.allowed
        assert decision.decisive_rule is not None
        assert decision.decisive_rule.origin is Origin.ACCOUNT_GRANT

    async def test_it_cannot_reach_a_global_node_even_if_widened(self) -> None:
        """Storage lets someone add a global pattern; resolution still refuses it."""
        permissions, _ = service(roles=widened("guild-admin", "build.**"))
        subject = Actor(account_id=7, guild_id=GUILD, discord_guild_admin=True)

        assert not await permissions.allows(subject, "build.submission.approve")


class TestLoadCost:
    async def test_the_owner_short_circuits_without_touching_the_store(self) -> None:
        permissions, store = service()

        assert await permissions.allows(Actor(account_id=1, is_bot_owner=True), "record.entry.rebuild")
        assert store.loads == 0

    async def test_resolving_many_nodes_costs_one_load(self) -> None:
        """Resolve a capability set from one repository load rather than loading once per node."""
        permissions, store = service(
            assignments=(PermissionRoleAssignment(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
        )
        held = await permissions.capabilities(
            Actor(account_id=7, guild_id=GUILD),
            ["build.submission.approve", "build.submission.reject", "tag.proposal.approve", "bot.tree.sync"],
        )

        assert store.loads == 1
        assert held == {"build.submission.approve", "build.submission.reject", "tag.proposal.approve"}


class TestGuildDelegation:
    @pytest.mark.parametrize(
        "pattern",
        ["settings.server.**", "settings.server.edit", "starboard.*.edit", "vote.**"],
    )
    def test_guild_only_patterns_are_delegable(self, pattern: str) -> None:
        permissions, _ = service()

        assert permissions.is_delegable_by_guild_admin(pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
            "settings.**",  # includes global runtime settings
            "settings.runtime.edit",
            "build.**",  # reaches global nodes
            "**",  # reaches everything
            "@destructive",  # spans both scopes
            "perm.grant.*",  # perm.grant.global is global
            "nonsense.**",  # reaches nothing at all
            "not a pattern",
        ],
    )
    def test_anything_reaching_a_global_node_is_refused(self, pattern: str) -> None:
        permissions, _ = service()

        assert not permissions.is_delegable_by_guild_admin(pattern)


async def test_an_expired_assignment_stops_applying() -> None:
    from whenever import Instant

    expired = Instant.from_utc(2020, 1, 1)
    permissions, _ = service(
        assignments=(PermissionRoleAssignment(role_id=ROLE_ID["trusted"], subject_account_id=7, expires_at=expired),),
    )

    decision = await permissions.check(Actor(account_id=7, guild_id=GUILD), "vote.log_delete.cast")

    assert decision.reason is Reason.DEFAULT


async def test_current_permission_rules_ignore_actor_capability_snapshots() -> None:
    permissions, _ = service()
    actor = Actor(account_id=7, capabilities=frozenset({"record.entry.rebuild"}), highest_permission_rank=999)
    assert not await permissions.allows(actor, "record.entry.rebuild")


@pytest.mark.parametrize("effect", [None, Effect.ALLOW, Effect.DENY, Effect.FORBID])
@pytest.mark.parametrize("bot_owner", [False, True])
async def test_transactional_explanations_preserve_acl_decisions(effect: Effect | None, bot_owner: bool) -> None:
    grants = (
        ()
        if effect is None
        else (PermissionGrant(pattern="build.submission.edit", effect=int(effect), subject_account_id=7),)
    )
    permissions, store = service(grants=grants)
    subject = Actor(account_id=7, is_bot_owner=bot_owner)
    async with permission_uow_factory(store)() as uow:
        decision = await permissions.check_in(uow, subject, BUILD_SUBMISSION_EDIT)
        assert decision.allowed is await permissions.allows_in(uow, subject, BUILD_SUBMISSION_EDIT)
    assert decision.allowed is (bot_owner or effect is Effect.ALLOW)
    assert store.loads == (0 if bot_owner else 2)
    if decision.allowed:
        with pytest.raises(ValueError, match="allowed permission decision"):
            PermissionDeniedError(decision)
        return
    error = PermissionDeniedError(decision)
    expected = {
        None: ("missing_grant", "your current permissions do not grant it"),
        Effect.DENY: ("explicit_deny", "an explicit deny rule blocks that permission"),
        Effect.FORBID: ("forbidden", "an explicit forbid rule blocks that permission"),
    }[effect]
    assert error.public_context == {
        "required_permission": "build.submission.edit",
        "permission_reason": expected[0],
    }
    assert error.public_detail() == f"This action requires build.submission.edit, but {expected[1]}."
    assert "account:7" not in error.public_detail()
    assert error.decisions == (decision,)


@pytest.mark.parametrize("mode", ["all", "any"])
@pytest.mark.parametrize("grant_first", [False, True])
@pytest.mark.parametrize("transactional", [False, True])
async def test_enforcement_reports_denied_nodes_without_rejecting_allowed_alternatives(
    mode: CheckMode, grant_first: bool, transactional: bool
) -> None:
    first = "build.submission.edit"
    second = "build.submission.approve"
    permissions, store = service(
        grants=(
            PermissionGrant(
                pattern=first, effect=int(Effect.ALLOW if grant_first else Effect.DENY), subject_account_id=7
            ),
            PermissionGrant(pattern=second, effect=int(Effect.FORBID), subject_account_id=7),
        )
    )
    actor = Actor(account_id=7)
    async with permission_uow_factory(store)() as uow:

        async def enforce() -> None:
            if transactional:
                await permissions.require_in(uow, actor, first, second, mode=mode)
            else:
                await permissions.require(actor, first, second, mode=mode)

        if mode == "any" and grant_first:
            await enforce()
        else:
            with pytest.raises(PermissionDeniedError) as denied:
                await enforce()
            assert denied.value.nodes == ((second,) if grant_first else (first, second))
            assert denied.value.mode == mode
            assert "explicit forbid" in denied.value.public_detail()
            assert "account:7" not in denied.value.public_detail()
            if not grant_first:
                assert "explicit deny" in denied.value.public_detail()
                assert ("at least one" if mode == "any" else "all of") in denied.value.public_detail()
    assert store.loads == 1


async def test_transactional_enforcement_rechecks_a_revoked_cached_grant() -> None:
    _, store = service(
        grants=(PermissionGrant(pattern="build.submission.edit", effect=int(Effect.ALLOW), subject_account_id=7),)
    )
    factory = permission_uow_factory(store)
    permissions = PermissionService(uow_factory=factory, cache=SubjectRuleCache())
    actor = Actor(account_id=7)
    await permissions.require(actor, "build.submission.edit")
    store.records = replace(store.records, epoch=2, grants=())
    async with factory() as uow:
        with pytest.raises(PermissionDeniedError) as denied:
            await permissions.require_in(uow, actor, "build.submission.edit")
    assert denied.value.public_context["permission_reason"] == "missing_grant"
    assert store.loads == 2


async def test_empty_enforcement_is_a_programming_error() -> None:
    permissions, store = service()
    with pytest.raises(ValueError, match="at least one node"):
        await permissions.require(Actor(is_bot_owner=True))
    async with permission_uow_factory(store)() as uow:
        with pytest.raises(ValueError, match="at least one node"):
            await permissions.require_in(uow, Actor(is_bot_owner=True), mode="any")
