"""Resolver behaviour, stated mostly as properties.

The precedence rules are small enough to read and subtle enough to get wrong in a
refactor, so the interesting assertions are quantified over generated rule sets
rather than written as a handful of examples.
"""

import dataclasses
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from whenever import Instant

from squid.permissions.domain import (
    CATALOGUE,
    Decision,
    Default,
    Effect,
    NodeScope,
    Origin,
    Pattern,
    Reason,
    RoleSpec,
    Rule,
    Subject,
    SubjectKind,
    expand_role,
    resolve,
    resolve_many,
    rules_from_role,
)

from .strategies import GUILD_ID, node_names, patterns, rule_sets, rules, selectors_for

MEMBER = Subject(account_id=1, guild_id=GUILD_ID)
OWNER = Subject(account_id=1, guild_id=GUILD_ID, is_bot_owner=True)


def allow(raw: str, **overrides: Any) -> Rule:
    return Rule(pattern=Pattern.parse(raw), effect=Effect.ALLOW, **overrides)


def deny(raw: str, **overrides: Any) -> Rule:
    return Rule(pattern=Pattern.parse(raw), effect=Effect.DENY, **overrides)


def signature(decision: Decision) -> tuple[object, ...]:
    """Everything about a decision that callers and `/perm can` depend on."""
    return (
        decision.node,
        decision.allowed,
        decision.reason,
        tuple((step.rule, step.decisive, step.lost_on) for step in decision.trace),
    )


class TestExamples:
    def test_specificity_beats_deny(self) -> None:
        """`build.**` denied, one leaf allowed: the narrower statement wins."""
        decision = resolve(
            "build.submission.approve",
            MEMBER,
            [deny("build.**"), allow("build.submission.approve")],
        )

        assert decision.allowed
        assert decision.trace[-1].lost_on == "specificity"

    def test_deny_breaks_a_complete_tie(self) -> None:
        """Two roles, same pattern, opposite effects: fail safe.

        This is the deliberate inversion of Discord, where allow would win.
        """
        role = {"subject_kind": SubjectKind.ACCOUNT, "origin": Origin.ROLE}
        decision = resolve(
            "build.submission.approve",
            MEMBER,
            [allow("build.submission.approve", **role), deny("build.submission.approve", **role)],
        )

        assert not decision.allowed
        assert decision.trace[-1].lost_on == "effect"

    def test_an_account_rule_outranks_a_discord_role_rule(self) -> None:
        decision = resolve(
            "build.submission.edit",
            MEMBER,
            [
                allow(
                    "build.submission.edit",
                    subject_kind=SubjectKind.DISCORD_ROLE,
                    origin=Origin.DISCORD_ROLE_GRANT,
                ),
                deny("build.submission.edit", subject_kind=SubjectKind.ACCOUNT),
            ],
        )

        assert not decision.allowed
        assert decision.trace[-1].lost_on == "subject"

    def test_an_explicit_rule_outranks_the_guild_admin_bridge(self) -> None:
        decision = resolve(
            "settings.server.edit",
            MEMBER,
            [
                allow("settings.**", origin=Origin.GUILD_ADMIN_BRIDGE, scope_guild_id=GUILD_ID),
                deny("settings.server.edit", origin=Origin.ACCOUNT_GRANT),
            ],
        )

        assert not decision.allowed

    def test_a_rule_from_another_guild_is_ignored(self) -> None:
        decision = resolve("settings.server.edit", MEMBER, [allow("settings.**", scope_guild_id=GUILD_ID + 1)])

        assert decision.reason is Reason.DEFAULT

    def test_an_expired_rule_is_ignored(self) -> None:
        now = Instant.from_utc(2026, 8, 13, 12)
        rule = allow("build.**", expires_at=now.subtract(seconds=1))

        assert resolve("build.submission.edit", MEMBER, [rule], now=now).reason is Reason.DEFAULT
        assert resolve("build.submission.edit", MEMBER, [rule], now=now.subtract(hours=1)).allowed

    def test_resolve_many_answers_a_whole_capability_set(self) -> None:
        rule = allow("vote.**", scope_guild_id=GUILD_ID)
        nodes = ["vote.log_delete.cast", "vote.weight.staff", "build.submission.approve"]

        assert resolve_many(nodes, MEMBER, [rule]) == {"vote.log_delete.cast", "vote.weight.staff"}


class TestRoleExpansion:
    def test_a_parent_subtracts_from_what_it_inherits(self) -> None:
        roles = {
            "helper": RoleSpec("helper", includes=("build.**",)),
            "moderator": RoleSpec("moderator", excludes=("@diagnostic",), includes_roles=("helper",)),
        }
        expansion = expand_role("moderator", roles)

        assert "build.**" in expansion.includes
        assert expansion.excluded == CATALOGUE.expand("@diagnostic")

    def test_a_childs_subtraction_survives_into_the_parent(self) -> None:
        roles = {
            "helper": RoleSpec("helper", includes=("build.**",), excludes=("build.submission.approve",)),
            "moderator": RoleSpec("moderator", includes_roles=("helper",)),
        }

        assert "build.submission.approve" in expand_role("moderator", roles).excluded

    def test_a_cycle_terminates_instead_of_raising(self) -> None:
        """A broken composition graph must not take every check down with it."""
        roles = {
            "a": RoleSpec("a", includes=("build.**",), includes_roles=("b",)),
            "b": RoleSpec("b", includes=("vote.**",), includes_roles=("a",)),
        }

        assert expand_role("a", roles).includes == {"build.**", "vote.**"}

    def test_depth_is_capped(self) -> None:
        roles = {
            str(index): RoleSpec(str(index), includes=(f"a{index}.b.c",), includes_roles=(str(index + 1),))
            for index in range(20)
        }
        expansion = expand_role("0", roles, max_depth=3)

        assert expansion.includes == {"a0.b.c", "a1.b.c", "a2.b.c"}


@given(name=node_names(), ruleset=rule_sets(), data=st.data())
def test_p1_the_decision_does_not_depend_on_rule_order(name: str, ruleset: list[Rule], data: st.DataObject) -> None:
    """P1: the rank tuple is a strict weak ordering, trace included.

    If this fails, the verdict depends on whatever order the database happened to
    return rows in.
    """
    shuffled = data.draw(st.permutations(ruleset))

    assert signature(resolve(name, MEMBER, ruleset)) == signature(resolve(name, MEMBER, shuffled))


@given(name=node_names(), data=st.data())
def test_p2_a_wildcard_grant_decides_a_leaf_as_an_exact_grant_would(name: str, data: st.DataObject) -> None:
    """P2: selecting a node by ancestor or by name reaches the same verdict."""
    selector = data.draw(st.sampled_from(selectors_for(name)))
    effect = data.draw(st.sampled_from((Effect.ALLOW, Effect.DENY)))
    rule = Rule(pattern=Pattern.parse(selector), effect=effect)
    exact = Rule(pattern=Pattern.parse(name), effect=effect)

    assert resolve(name, MEMBER, [rule]).allowed == resolve(name, MEMBER, [exact]).allowed


@given(name=node_names(), ruleset=rule_sets(include_forbid=False))
def test_p4_a_guild_scoped_rule_never_allows_a_global_node(name: str, ruleset: list[Rule]) -> None:
    """P4: guild-scoped authority cannot reach the shared build database."""
    guild_only = [dataclasses.replace(rule, scope_guild_id=GUILD_ID) for rule in ruleset]
    decision = resolve(name, MEMBER, guild_only)

    if CATALOGUE[name].scope is NodeScope.GLOBAL:
        assert decision.reason is Reason.DEFAULT
        assert decision.allowed is (CATALOGUE[name].default is Default.ALLOW)


@given(pattern=patterns(), ruleset=rule_sets(include_forbid=False), name=node_names())
def test_p5_a_delegable_grant_cannot_change_a_global_decision(pattern: str, ruleset: list[Rule], name: str) -> None:
    """P5: whatever the guild-delegation validator accepts is safe to add.

    The validator's rule is "every scope this pattern reaches is GUILD"; this
    checks that accepting on that basis really does leave global nodes alone, for
    every rule set and every node.
    """
    assume(not CATALOGUE.scopes_reached(pattern) - {NodeScope.GUILD})
    delegated = Rule(pattern=Pattern.parse(pattern), effect=Effect.ALLOW, scope_guild_id=GUILD_ID)

    if CATALOGUE[name].scope is NodeScope.GLOBAL:
        assert resolve(name, MEMBER, [*ruleset, delegated]).allowed == resolve(name, MEMBER, ruleset).allowed


@given(name=node_names(), data=st.data())
def test_p6_a_role_subtraction_is_not_a_deny(name: str, data: st.DataObject) -> None:
    """P6: Azure `NotActions` semantics.

    A role that excludes a node withholds it; it must not stop a different role
    from conferring it.
    """
    broad = data.draw(st.sampled_from([s for s in selectors_for(name) if s != name]))
    subtracting = RoleSpec("subtracting", includes=(broad,), excludes=(name,))
    granting = RoleSpec("granting", includes=(name,))
    specs = {"subtracting": subtracting, "granting": granting}

    ruleset = [
        *rules_from_role(expand_role("subtracting", specs), subject_kind=SubjectKind.ACCOUNT, source="role:a"),
        *rules_from_role(expand_role("granting", specs), subject_kind=SubjectKind.ACCOUNT, source="role:b"),
    ]

    assert resolve(name, MEMBER, ruleset).allowed
    # ...and alone, the subtracting role really does withhold it.
    alone = rules_from_role(expand_role("subtracting", specs), subject_kind=SubjectKind.ACCOUNT)
    assert resolve(name, MEMBER, list(alone)).reason is Reason.DEFAULT


@given(keys=st.lists(st.sampled_from("abcde"), min_size=1, max_size=5, unique=True), data=st.data())
def test_p7_role_expansion_terminates_and_ignores_edge_order(keys: list[str], data: st.DataObject) -> None:
    """P7: composition graphs are safe to load even when cyclic."""
    specs: dict[str, RoleSpec] = {}
    for key in keys:
        edges = data.draw(st.lists(st.sampled_from(keys), max_size=3, unique=True))
        specs[key] = RoleSpec(key, includes=(f"{key}x.b.c",), includes_roles=tuple(edges))

    first = expand_role(keys[0], specs)
    reordered = {
        key: dataclasses.replace(spec, includes_roles=tuple(reversed(spec.includes_roles)))
        for key, spec in specs.items()
    }

    assert expand_role(keys[0], reordered) == first


@given(name=node_names(), ruleset=rule_sets(), pattern=patterns())
def test_p8_forbid_denies_regardless_of_everything_else(name: str, ruleset: list[Rule], pattern: str) -> None:
    """P8: the emergency stop does not have to win a specificity argument."""
    forbid = Rule(pattern=Pattern.parse(pattern), effect=Effect.FORBID)
    decision = resolve(name, MEMBER, [*ruleset, forbid])

    if forbid.matches(CATALOGUE[name]):
        assert not decision.allowed
        assert decision.reason is Reason.FORBIDDEN


@given(name=node_names(), ruleset=rule_sets())
def test_p9_the_owner_is_allowed_everything(name: str, ruleset: list[Rule]) -> None:
    """P9: no stored rule, forbid included, can lock the owner out."""
    decision = resolve(name, OWNER, [*ruleset, Rule(pattern=Pattern.parse("**"), effect=Effect.FORBID)])

    assert decision.allowed
    assert decision.reason is Reason.OWNER


@given(rule=rules(), label=st.text(max_size=8), note=st.text(max_size=8))
def test_p10_rank_depends_only_on_the_precedence_components(rule: Rule, label: str, note: str) -> None:
    """P10: role rank, and everything else cosmetic, stays out of resolution.

    Role rank governs who may edit which role and nothing more. It never reaches
    `Rule`, so the closest structural guard is that a rule's precedence is a
    function of exactly the five documented components.
    """
    relabelled = dataclasses.replace(rule, source=label, via=note)

    assert relabelled.rank() == rule.rank()


@given(name=node_names(), ruleset=rule_sets())
def test_p12_an_exact_rule_beats_any_wildcard_of_the_opposite_effect(name: str, ruleset: list[Rule]) -> None:
    """P12: specificity dominance, the rule the whole design leans on."""
    exact = Rule(pattern=Pattern.parse(name), effect=Effect.DENY)
    node = CATALOGUE[name]
    assume(not any(rule.effect is Effect.FORBID and rule.matches(node) for rule in ruleset))
    # A rule naming the same leaf can still outrank it on scope, subject or origin.
    assume(not any(rule.pattern.raw == name and rule.rank() > exact.rank() for rule in ruleset))

    assert not resolve(name, MEMBER, [*ruleset, exact]).allowed


@given(name=node_names(), ruleset=rule_sets())
def test_p13_the_trace_explains_the_decision_it_came_with(name: str, ruleset: list[Rule]) -> None:
    """P13: replaying the winning step alone reproduces the verdict.

    `/perm can` renders this trace, so a trace that disagrees with its own
    decision would be a lie told to whoever is debugging a permission problem.
    """
    decision = resolve(name, MEMBER, ruleset)

    if decision.reason in (Reason.OWNER, Reason.DEFAULT):
        assert decision.trace == ()
        return

    winner = decision.decisive_rule
    assert winner is not None
    assert resolve(name, MEMBER, [winner]).allowed == decision.allowed
    if decision.reason is Reason.RULE:
        assert all(step.lost_on is not None for step in decision.trace if not step.decisive)


@given(name=node_names())
def test_p14_an_empty_rule_set_falls_through_to_the_catalogue_default(name: str) -> None:
    decision = resolve(name, MEMBER, [])

    assert decision.reason is Reason.DEFAULT
    assert decision.allowed is (CATALOGUE[name].default is Default.ALLOW)
    assert decision.trace == ()


@pytest.mark.parametrize("subject", [MEMBER, Subject(guild_id=GUILD_ID)])
def test_an_unauthenticated_subject_resolves_without_an_account(subject: Subject) -> None:
    """A permission check must never be the thing that creates an account row."""
    rule = allow("settings.**", subject_kind=SubjectKind.DISCORD_ROLE, scope_guild_id=GUILD_ID)

    assert resolve("settings.server.edit", subject, [rule]).allowed
