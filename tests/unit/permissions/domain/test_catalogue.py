"""The node catalogue's structure and the built-in role definitions."""

import pytest

from squid.core.i18n import tr
from squid.permissions.domain import (
    BUILTIN_ROLES,
    BUILTIN_ROLES_BY_KEY,
    CATALOGUE,
    CatalogueBuilder,
    CatalogueError,
    Default,
    NodeScope,
    RoleSpec,
    UnknownPermissionNodeError,
    expand_role,
)
from squid.permissions.domain.catalogue import MAX_SEGMENTS, MIN_SEGMENTS


def builtin_leaves(key: str) -> frozenset[str]:
    """The node names a built-in role resolves to right now."""
    roles = {role.key: RoleSpec(role.key, role.includes, role.excludes) for role in BUILTIN_ROLES}
    expansion = expand_role(key, roles)
    reached = {name for pattern in expansion.includes for name in CATALOGUE.expand(pattern)}
    return frozenset(reached - expansion.excluded)


def test_node_names_follow_the_convention() -> None:
    for node in CATALOGUE:
        segments = node.segments
        assert MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS, node.name
        assert all(segment.replace("_", "a").isalnum() for segment in segments), node.name
        assert node.name.islower(), node.name


def test_every_node_is_described() -> None:
    """Descriptions are user-facing: they are what `/perm nodes` shows."""
    assert all(tr(node.description).strip() for node in CATALOGUE)


def test_nodes_can_form_capability_sets_after_description_localization() -> None:
    capabilities = frozenset(CATALOGUE)

    assert all(node in capabilities for node in CATALOGUE)


def test_unknown_nodes_raise_rather_than_deny() -> None:
    with pytest.raises(UnknownPermissionNodeError):
        _ = CATALOGUE["build.submission.nonexistent"]


class TestBuilder:
    def test_rejects_duplicate_names(self) -> None:
        builder = CatalogueBuilder()
        builder.node("a.b.c", NodeScope.GUILD, tr(t"first"))

        with pytest.raises(CatalogueError, match="Duplicate"):
            builder.node("a.b.c", NodeScope.GUILD, tr(t"second"))

    @pytest.mark.parametrize("name", ["a.**", "a.*.c", "@destructive"])
    def test_rejects_patterns_as_node_names(self, name: str) -> None:
        builder = CatalogueBuilder()

        with pytest.raises(CatalogueError, match="concrete name"):
            builder.node(name, NodeScope.GUILD, tr(t"nope"))

    def test_rejects_a_node_that_is_also_a_namespace(self) -> None:
        """`a.b` alongside `a.b.c` would make `a.b.**` ambiguous."""
        builder = CatalogueBuilder()
        builder.node("a.b", NodeScope.GUILD, tr(t"interior"))
        builder.node("a.b.c", NodeScope.GUILD, tr(t"leaf"))

        with pytest.raises(CatalogueError, match="also a namespace"):
            builder.build()


class TestBuiltinRoles:
    def test_owner_holds_everything_via_the_root_pattern(self) -> None:
        assert BUILTIN_ROLES_BY_KEY["owner"].includes == ("**",)
        assert BUILTIN_ROLES_BY_KEY["owner"].excludes == ()
        assert builtin_leaves("owner") == frozenset(CATALOGUE.names)

    def test_guild_admin_reaches_guild_nodes_only(self) -> None:
        """The Manage Server bridge must never confer cross-guild authority."""
        scopes = {CATALOGUE[name].scope for name in builtin_leaves("guild-admin")}

        assert scopes == {NodeScope.GUILD}

    def test_global_admin_stops_short_of_the_owner_only_powers(self) -> None:
        leaves = builtin_leaves("global-admin")

        assert not leaves & CATALOGUE.expand("@destructive")
        assert not leaves & CATALOGUE.expand("bot.**")
        assert "perm.grant.global" not in leaves
        assert "role.definition.manage" not in leaves

    def test_global_admin_withholds_nothing_beyond_those(self) -> None:
        """Guards the include list, which is where a node is silently forgotten.

        The old `check_is_global_admin` tier passed `check_is_server_admin` too,
        so a global administrator keeps every guild-scoped node as well; an
        include list that misses a namespace shows up here rather than as a
        support question.
        """
        withheld = frozenset(CATALOGUE.names) - builtin_leaves("global-admin")
        owner_only = (
            CATALOGUE.expand("@destructive")
            | CATALOGUE.expand("bot.**")
            | {"perm.grant.global", "role.definition.manage"}
        )
        ungrantable = {node.name for node in CATALOGUE if node.default is Default.ALLOW}

        assert withheld <= owner_only | ungrantable

    def test_trusted_matches_the_old_tier(self) -> None:
        assert builtin_leaves("trusted") == {
            "build.schematic.detect_lattice",
            "build.schematic.measure_timing",
            "vote.log_delete.cast",
            "vote.weight.staff",
        }

    def test_ranks_are_distinct_and_descending(self) -> None:
        ranks = [role.rank for role in BUILTIN_ROLES]

        assert ranks == sorted(ranks, reverse=True)
        assert len(set(ranks)) == len(ranks)

    def test_all_builtins_are_protected(self) -> None:
        assert all(role.protected for role in BUILTIN_ROLES)
