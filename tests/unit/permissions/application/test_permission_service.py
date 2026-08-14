"""PermissionService: assembling stored rows into resolver rules."""

from collections.abc import Iterable

import pytest

from squid.permissions.application import PermissionService
from squid.permissions.application.ports import AssignmentRecord, GrantRecord, RoleRecord, SubjectRecords
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Effect, Origin, Reason, Subject

GUILD = 555

BUILTIN_ROWS = tuple(
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
ROLE_ID = {row.builtin_key: row.id for row in BUILTIN_ROWS}


class FakeStore:
    """Counts loads, so the N+1 guard has something to assert on."""

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
    roles: tuple[RoleRecord, ...] = BUILTIN_ROWS,
    grants: tuple[GrantRecord, ...] = (),
    assignments: tuple[AssignmentRecord, ...] = (),
) -> tuple[PermissionService, FakeStore]:
    store = FakeStore(SubjectRecords(epoch=1, roles=roles, grants=grants, assignments=assignments))
    return PermissionService(store), store


async def test_a_builtin_role_uses_the_pattern_list_defined_in_code() -> None:
    """The seeded rows carry no patterns; the catalogue module supplies them."""
    permissions, _ = service(
        assignments=(AssignmentRecord(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
    )
    subject = Subject(account_id=7, guild_id=GUILD)

    assert await permissions.allows(subject, "build.submission.approve")
    assert not await permissions.allows(subject, "record.entry.rebuild")


def widened(key: str, *patterns: str) -> tuple[RoleRecord, ...]:
    """The built-in rows, with stored include patterns added to one of them."""
    return tuple(
        RoleRecord(
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
        assignments=(AssignmentRecord(role_id=ROLE_ID["guild-admin"], subject_account_id=7),),
    )
    subject = Subject(account_id=7, guild_id=GUILD)

    assert await permissions.allows(subject, "perm.audit.view")
    assert await permissions.allows(subject, "settings.server.edit")


async def test_a_stored_pattern_cannot_punch_through_a_builtins_exclude() -> None:
    """The code-level excludes are a boundary, not a default.

    `global-admin` withholds `bot.**` and `@destructive` deliberately, so someone
    with write access to `permission_role_patterns` must not be able to hand
    themselves the owner-only surface by adding an include row. Widening past the
    boundary is a deploy, or a separate role.
    """
    permissions, _ = service(
        roles=widened("global-admin", "bot.**", "record.entry.rebuild"),
        assignments=(AssignmentRecord(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
    )
    subject = Subject(account_id=7, guild_id=GUILD)

    assert not await permissions.allows(subject, "bot.tree.sync")
    assert not await permissions.allows(subject, "record.entry.rebuild")


async def test_a_role_assignment_to_a_discord_role_reaches_its_holders() -> None:
    permissions, _ = service(
        assignments=(AssignmentRecord(role_id=ROLE_ID["trusted"], subject_role_id=99, subject_guild_id=GUILD),),
    )
    subject = Subject(account_id=7, discord_role_ids=frozenset({99}), guild_id=GUILD)

    assert await permissions.allows(subject, "vote.log_delete.cast")


class TestGuildAdminBridge:
    async def test_manage_server_confers_the_guild_nodes(self) -> None:
        permissions, _ = service()
        subject = Subject(account_id=7, guild_id=GUILD, discord_guild_admin=True)

        assert await permissions.allows(subject, "settings.server.edit")
        assert not await permissions.allows(subject, "build.submission.approve")

    async def test_it_is_absent_without_manage_server(self) -> None:
        permissions, _ = service()
        subject = Subject(account_id=7, guild_id=GUILD)

        assert not await permissions.allows(subject, "settings.server.edit")

    async def test_an_explicit_deny_outranks_it(self) -> None:
        """The permission system is evaluated first; Discord is only a default."""
        permissions, _ = service(
            grants=(GrantRecord(pattern="settings.server.edit", effect=int(Effect.DENY), subject_account_id=7),),
        )
        subject = Subject(account_id=7, guild_id=GUILD, discord_guild_admin=True)
        decision = await permissions.check(subject, "settings.server.edit")

        assert not decision.allowed
        assert decision.decisive_rule is not None
        assert decision.decisive_rule.origin is Origin.ACCOUNT_GRANT

    async def test_it_cannot_reach_a_global_node_even_if_widened(self) -> None:
        """Storage lets someone add a global pattern; resolution still refuses it."""
        permissions, _ = service(roles=widened("guild-admin", "build.**"))
        subject = Subject(account_id=7, guild_id=GUILD, discord_guild_admin=True)

        assert not await permissions.allows(subject, "build.submission.approve")


class TestLoadCost:
    async def test_the_owner_short_circuits_without_touching_the_store(self) -> None:
        permissions, store = service()

        assert await permissions.allows(Subject(account_id=1, is_bot_owner=True), "record.entry.rebuild")
        assert store.loads == 0

    async def test_resolving_many_nodes_costs_one_load(self) -> None:
        """The N+1 guard: today's tiers pay two or three round trips per check."""
        permissions, store = service(
            assignments=(AssignmentRecord(role_id=ROLE_ID["global-admin"], subject_account_id=7),),
        )
        held = await permissions.capabilities(
            Subject(account_id=7, guild_id=GUILD),
            ["build.submission.approve", "build.submission.reject", "tag.proposal.approve", "bot.tree.sync"],
        )

        assert store.loads == 1
        assert held == {"build.submission.approve", "build.submission.reject", "tag.proposal.approve"}


class TestGuildDelegation:
    @pytest.mark.parametrize(
        "pattern",
        ["settings.**", "settings.server.edit", "starboard.*.edit", "vote.**"],
    )
    def test_guild_only_patterns_are_delegable(self, pattern: str) -> None:
        permissions, _ = service()

        assert permissions.is_delegable_by_guild_admin(pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
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
        assignments=(AssignmentRecord(role_id=ROLE_ID["trusted"], subject_account_id=7, expires_at=expired),),
    )

    decision = await permissions.check(Subject(account_id=7, guild_id=GUILD), "vote.log_delete.cast")

    assert decision.reason is Reason.DEFAULT
