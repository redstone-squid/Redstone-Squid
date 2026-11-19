"""Opaque widgets participate only through declared, independently checked contracts."""

from dataclasses import dataclass, field
from typing import override

import pytest

import squid_ui as sl
from squid_i18n import tree
from squid_ui.authoring.presentations import AtomicNode, budget
from squid_ui.content.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.planning.composition_v2 import V2CompositionRules
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.ext.discord.target_types import ComponentsV2Target, DiscordAdapter
from squid_ui.planning import AutomaticComposition, CompositionRejectedError, FixedComposition, JointInfeasibleError
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.composition import (
    AtomicContract,
    CandidateComposition,
    CapacityExceeded,
    Evaluation,
    Placement,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    UnsupportedCapability,
)
from squid_ui.planning.joint_search import JointUnsupportedError
from squid_ui.planning.realizations import DownloadRealization, Realization, TextRealization
from squid_ui.planning.resources import Axis, ResourceCost

TARGET = components_v2_target(AdapterProfile(DiscordAdapter, "composition.atoms", ">=1"))


@dataclass(frozen=True, slots=True)
class Badge(AtomicNode):
    label: str


@dataclass(frozen=True, slots=True)
class FileBadge(AtomicNode):
    asset: Asset


@dataclass(frozen=True, slots=True)
class BadgeRules(V2CompositionRules[ComponentsV2Target, object]):
    cost_error: int = 0
    requires: frozenset[str] = frozenset()
    placements: frozenset[Placement] = frozenset(Placement)
    calls: list[str] = field(default_factory=list, compare=False)

    @override
    def offers(
        self, occurrence: SemanticOccurrence[ComponentsV2Target, object], context: PlanningContext
    ) -> tuple[RealizationOffer[ComponentsV2Target, object, Realization], ...]:
        value = occurrence.graph.units[occurrence.reference].value
        if isinstance(value, Badge):
            context.work.consume()
            return (
                RealizationOffer(
                    occurrence,
                    "badge",
                    TextRealization((tree.literal(value.label),)),
                    effects=occurrence.scope_effects(value.effects),
                    atomic=AtomicContract(
                        ResourceCost(
                            {
                                Axis.DISPLAY_TEXT: len(value.label) + self.cost_error,
                                Axis.COMPONENTS: int(bool(value.label)),
                            }
                        ),
                        capabilities=self.requires,
                        placements=self.placements,
                    ),
                ),
            )
        if isinstance(value, FileBadge):
            context.work.consume()
            return (
                RealizationOffer(
                    occurrence,
                    "file-badge",
                    DownloadRealization(value.asset, (), (), spoiler=False),
                    effects=occurrence.scope_effects(value.effects),
                    atomic=AtomicContract(
                        ResourceCost({Axis.COMPONENTS: 1}),
                        {ResourceIdentity("attachment", value.asset.key): ResourceCost({Axis.ATTACHMENTS: 1})},
                    ),
                ),
            )
        offers = super().offers(occurrence, context)
        assert isinstance(offers, tuple)
        return offers

    @override
    def evaluate(
        self, candidate: CandidateComposition[ComponentsV2Target, object, Realization], context: PlanningContext
    ) -> Evaluation[object, discord_scene.ComponentsV2]:
        self.calls.append("prepare")
        return super().evaluate(candidate, context)

    @override
    def build(self, preparation: discord_scene.ComponentsV2, context: PlanningContext) -> discord_scene.ComponentsV2:
        self.calls.append("build")
        return super().build(preparation, context)

    @override
    def validate(
        self,
        body: discord_scene.ComponentsV2,
        expected: Evaluation[object, discord_scene.ComponentsV2],
        context: PlanningContext,
    ) -> None:
        self.calls.append("validate")
        super().validate(body, expected, context)


def test_custom_atomic_rules_use_the_public_composition_boundary() -> None:
    rules = BadgeRules()
    result = sl.planning.plan(Badge("badge", "Ready"), target=TARGET, composition=FixedComposition(rules))
    assert result.scene.body.children == (discord_scene.Text("Ready", sl.text.Markup.DISCORD_MARKDOWN),)
    assert rules.calls == ["prepare", "build", "validate"]


def test_atomic_declared_cost_must_match_evaluation() -> None:
    rules = BadgeRules(cost_error=1)
    with pytest.raises(DrawInvariantError, match="atomic evaluation"):
        sl.planning.plan(Badge("badge", "Ready"), target=TARGET, composition=FixedComposition(rules))
    assert rules.calls == ["prepare"]


def test_atomic_capabilities_are_checked_before_preparing_output() -> None:
    rules = BadgeRules(requires=frozenset({"test.missing"}))
    with pytest.raises(CompositionRejectedError):
        sl.planning.plan(Badge("badge", "Ready"), target=TARGET, composition=FixedComposition(rules))
    assert rules.calls == []


def test_atomic_placement_constraints_apply_to_repeated_chrome() -> None:
    rules = BadgeRules(placements=frozenset({Placement.CONTENT}))
    region = sl.region(sl.paragraph("Body"), key="region", title="Region", header=(Badge("badge", "Header"),))
    with pytest.raises(CompositionRejectedError):
        sl.planning.plan(region, target=TARGET, composition=FixedComposition(rules))
    assert rules.calls == []


def test_atomic_shared_resources_deduplicate_and_obey_enclosing_budgets() -> None:
    asset = sl.file(b"data", filename="report.txt")
    content = budget(sl.stack(FileBadge("one", asset), FileBadge("two", asset)), limits={Axis.ATTACHMENTS: 1})
    result = sl.planning.plan(content, target=TARGET, composition=FixedComposition(BadgeRules()))
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.cost.get(Axis.COMPONENTS) == 2
    assert result.resources[f"asset:{asset.key}"] is asset


def test_joint_search_rejects_atomic_capabilities_during_discovery() -> None:
    rules = BadgeRules(requires=frozenset({"test.missing"}))
    with pytest.raises(JointUnsupportedError) as caught:
        sl.planning.plan(Badge("badge", "Ready"), target=TARGET, composition=AutomaticComposition(rules))

    assert rules.calls == []
    assert any(isinstance(violation, UnsupportedCapability) for violation in caught.value.diagnostics.violations)


def test_joint_search_rejects_the_cost_of_atoms_that_must_coexist() -> None:
    rules = BadgeRules()
    content = sl.stack(*(Badge(f"badge-{index}", "Ready") for index in range(41)))
    with pytest.raises(JointInfeasibleError) as caught:
        sl.planning.plan(content, target=TARGET, composition=AutomaticComposition(rules))

    assert rules.calls == []
    assert any(
        isinstance(violation, CapacityExceeded)
        and (violation.axis, violation.used, violation.capacity) == (Axis.COMPONENTS, 41, 40)
        for violation in caught.value.diagnostics.violations
    )


def test_joint_search_can_split_atoms_across_pages() -> None:
    content = sl.region(Badge("first", "a" * 2500), Badge("second", "b" * 2500), key="badges", title="Badges")
    result = sl.planning.plan(content, target=TARGET, composition=AutomaticComposition(BadgeRules()))

    assert result.allocation is not None
    (region,) = result.allocation.regions.values()
    assert region.cuts == (1, 2)


def test_joint_search_counts_shared_atomic_cost_once() -> None:
    asset = sl.file(b"data", filename="report.txt")
    content = sl.stack(FileBadge("one", asset), FileBadge("two", asset))
    target = TARGET.reserve(ResourceCost({Axis.ATTACHMENTS: TARGET.limits.attachments - 1}))
    result = sl.planning.plan(
        content, target=target, composition=AutomaticComposition(BadgeRules(limits=target.limits))
    )

    assert result.cost.get(Axis.ATTACHMENTS) == 1


def test_builtin_rules_do_not_guess_an_opaque_widgets_meaning() -> None:
    with pytest.raises(CompositionRejectedError):
        sl.planning.plan(Badge("badge", "Ready"), target=TARGET, composition=sl.planning.CompositionOptions())


def test_atomic_effects_remain_scoped_and_require_non_strict_admission() -> None:
    node = Badge("badge", "", effects=(sl.omit("badge"),))
    with pytest.raises(CompositionRejectedError):
        sl.planning.plan(node, target=TARGET, composition=FixedComposition(BadgeRules()), strict=True)
    result = sl.planning.plan(node, target=TARGET, composition=FixedComposition(BadgeRules()), strict=False)
    assert not result.scene.body.children
    assert not result.lossless
    assert result.effects == (sl.omit(sl.ref("@document", "badge")),)
    assert any(event.code == "composition.effect" and event.path == "@document/badge" for event in result.report.events)
