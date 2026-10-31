"""Variant fidelity: which rungs actually cost the reader something."""

import pytest

from squid_layouts import LayoutDegradedError, plan
from squid_layouts.discord import V2_LIMITS as LIMITS
from squid_layouts.planning import TargetProfile
from squid_layouts.planning.degradation import DegradationEffect, DegradationProfile
from squid_layouts.primitives import Fidelity, Panel, Text, Variant, Variants
from squid_layouts.scene.model import SceneText

TARGET = TargetProfile("test", 1, limits=LIMITS)


def oversized() -> Panel:
    """A rung no target budget accepts: 40 texts plus their container is 41 components."""
    return Panel(children=tuple(Text(f"line {index}") for index in range(LIMITS.total_components)))


def ladder(*rungs: Variant) -> Variants:
    return Variants((Variant((oversized(),)), *rungs))


def events(document) -> list[tuple[str, str]]:
    report = plan(document, target=TARGET).report
    return [(event.code, event.severity.value) for event in report.events]


class TestStrictMode:
    def test_an_exact_later_rung_survives_strict_planning(self) -> None:
        """Stepping is not loss. A smaller faithful shape must not fail `strict=True`."""
        scene = plan(ladder(Variant((Text("compact"),))), target=TARGET, strict=True).scene

        assert scene.components_v2.children == (SceneText("compact"),)

    def test_a_reformatted_rung_is_rejected_by_strict_planning(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.REFORMATTED))

        with pytest.raises(LayoutDegradedError, match="stepped to reformatted variant 2 of 2"):
            plan(document, target=TARGET, strict=True)

    def test_a_lossy_rung_is_rejected_by_strict_planning(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.LOSSY))

        with pytest.raises(LayoutDegradedError, match="stepped to lossy variant 2 of 2"):
            plan(document, target=TARGET, strict=True)


class TestReporting:
    def test_an_exact_rung_reports_adaptation_rather_than_degradation(self) -> None:
        """Still visible — the reader's shape did change — but not counted as loss."""
        assert events(ladder(Variant((Text("compact"),)))) == [("layout.adaptation.variant_step", "adaptation")]

    def test_a_reformatted_rung_reports_its_own_code(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.REFORMATTED))

        assert events(document) == [("layout.degradation.variant_reformatted", "degradation")]

    def test_a_lossy_rung_reports_its_own_code(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.LOSSY))

        assert events(document) == [("layout.degradation.variant_lossy", "degradation")]


class TestOrdering:
    def test_an_exact_late_rung_beats_a_reformatted_early_one(self) -> None:
        """Fidelity is compared before preference, so rung order cannot override it."""
        document = ladder(
            Variant((Text("reformatted"),), fidelity=Fidelity.REFORMATTED),
            Variant((Text("exact"),)),
        )

        assert plan(document, target=TARGET).scene.components_v2.children == (SceneText("exact"),)

    def test_a_reformatted_rung_beats_a_lossy_earlier_one(self) -> None:
        document = ladder(
            Variant((Text("lossy"),), fidelity=Fidelity.LOSSY),
            Variant((Text("reformatted"),), fidelity=Fidelity.REFORMATTED),
        )

        assert plan(document, target=TARGET).scene.components_v2.children == (SceneText("reformatted"),)

    def test_the_first_exact_rung_still_wins_among_equals(self) -> None:
        """With fidelity tied, rung distance decides — that is what preference means."""
        document = ladder(Variant((Text("first"),)), Variant((Text("second"),)))

        assert plan(document, target=TARGET).scene.components_v2.children == (SceneText("first"),)


class TestProfileAxes:
    def test_a_lossy_region_outranks_every_other_loss_at_its_priority(self) -> None:
        lossy = DegradationProfile().with_effect(DegradationEffect(0, "$.a", lossy_nodes=1))
        everything_else = DegradationProfile().with_effect(
            DegradationEffect(0, "$.a", semantic_steps=9, truncated_chars=9, spilled_items=9, dropped_nodes=9)
        )

        assert everything_else < lossy

    def test_reformatting_ranks_below_truncation_because_it_keeps_every_character(self) -> None:
        reformatted = DegradationProfile().with_effect(DegradationEffect(0, "$.a", reformatted_nodes=9))
        truncated = DegradationProfile().with_effect(DegradationEffect(0, "$.a", truncated_chars=1))

        assert reformatted < truncated
