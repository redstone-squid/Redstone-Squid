"""Variant fidelity: which rungs actually cost the reader something."""

from dataclasses import replace

import pytest

from squid_ui import paragraph, scene
from squid_ui.authoring.presentations import Replacement, corresponds, losses, ref
from squid_ui.content.text import Markup
from squid_ui.ext.discord import V2_LIMITS as LIMITS
from squid_ui.ext.discord import testing as sd
from squid_ui.planning import plan
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.primitives import Fidelity, Panel, Text, Variant, Variants

TARGET = sd.target_profile("test")


def oversized() -> Panel:
    """Exceed the default V2 budget with 40 text nodes plus their container."""
    return Panel(children=tuple(Text(f"line {index}") for index in range(LIMITS.total_components)))


def ladder(*rungs: Variant) -> Variants:
    return Variants(
        tuple(
            replace(
                rung,
                correspondence=(corresponds("@content", "source"),),
                contract=losses(Replacement(ref("source"))) if rung.fidelity is Fidelity.LOSSY else None,
            )
            for rung in (Variant((oversized(),)), *rungs)
        ),
        content=paragraph("Complete fixture content", key="source"),
    )


def events(document) -> list[tuple[str, str]]:
    report = plan(document, target=TARGET).report
    return [(event.code, event.severity.value) for event in report.events]


class TestStrictMode:
    def test_an_exact_later_rung_survives_strict_planning(self) -> None:
        """Stepping is not loss. A smaller faithful shape must not fail `strict=True`."""
        document = plan(ladder(Variant((Text("compact"),))), target=TARGET, strict=True).scene

        assert document.components_v2.children == (scene.Text("compact", markup=Markup.DISCORD_MARKDOWN),)

    def test_a_reformatted_rung_preserves_content_in_strict_planning(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.REFORMATTED))

        result = plan(document, target=TARGET, strict=True)
        assert result.allocation is not None
        assert not result.report.events

    def test_a_lossy_rung_is_rejected_by_strict_planning(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.LOSSY))

        with pytest.raises(JointInfeasibleError):
            plan(document, target=TARGET, strict=True)


class TestReporting:
    def test_an_exact_rung_reports_adaptation_rather_than_degradation(self) -> None:
        """Still visible — the reader's shape did change — but not counted as loss."""
        assert events(ladder(Variant((Text("compact"),)))) == []

    def test_a_reformatted_rung_reports_its_own_code(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.REFORMATTED))

        assert events(document) == []

    def test_a_lossy_rung_reports_its_own_code(self) -> None:
        document = ladder(Variant((Text("compact"),), fidelity=Fidelity.LOSSY))

        assert events(document) == [("composition.effect", "degradation")]


class TestOrdering:
    def test_an_exact_late_rung_beats_a_reformatted_early_one(self) -> None:
        """Fidelity is compared before preference, so rung order cannot override it."""
        document = ladder(
            Variant((Text("reformatted"),), fidelity=Fidelity.REFORMATTED),
            Variant((Text("exact"),)),
        )

        assert plan(document, target=TARGET).scene.components_v2.children == (
            scene.Text("exact", markup=Markup.DISCORD_MARKDOWN),
        )

    def test_a_reformatted_rung_beats_a_lossy_earlier_one(self) -> None:
        document = ladder(
            Variant((Text("lossy"),), fidelity=Fidelity.LOSSY),
            Variant((Text("reformatted"),), fidelity=Fidelity.REFORMATTED),
        )

        assert plan(document, target=TARGET).scene.components_v2.children == (
            scene.Text("reformatted", markup=Markup.DISCORD_MARKDOWN),
        )

    def test_the_first_exact_rung_still_wins_among_equals(self) -> None:
        """With fidelity tied, rung distance decides — that is what preference means."""
        document = ladder(Variant((Text("first"),)), Variant((Text("second"),)))

        assert plan(document, target=TARGET).scene.components_v2.children == (
            scene.Text("first", markup=Markup.DISCORD_MARKDOWN),
        )
