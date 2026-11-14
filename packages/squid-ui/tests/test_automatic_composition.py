import logging
from dataclasses import replace
from typing import Literal

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.handlers import FrontendHandler, handlers
from squid_ui.planning import (
    AutomaticComposition,
    CompositionOptions,
    JointExhaustedError,
    JointUnsupportedError,
    SearchCompletion,
)
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentation import PresentationState
from squid_ui.target_types import ComponentsV2Target, DiscordAdapter, SlackAdapter

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "joint.discord", ">=1"))
SLACK = sl.slack.message_target(adapter=AdapterProfile(SlackAdapter, "joint.slack", ">=1"))


def reject_legacy(*args: object, **kwargs: object) -> None:
    pytest.fail("automatic composition called a legacy complete planner")


@pytest.mark.parametrize("target", ["v2", "slack", "html"])
def test_public_automatic_planning_and_page_selection(
    target: Literal["v2", "slack", "html"], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("squid_ui.planning.planner.plan_regions", reject_legacy)
    monkeypatch.setattr("squid_ui.planning.html_planner.HtmlPlanner.plan", reject_legacy)
    monkeypatch.setattr("squid_ui.planning.slack_planner.SlackPlanner.plan", reject_legacy)
    node = sl.region(
        sl.table(
            sl.columns(sl.column("Name", key="name")),
            *(sl.table_row(f"Row {i}", key=str(i)) for i in range(3)),
            key="table",
        ),
        key="r",
        title="R",
    )
    identity = OccurrenceId(("root", "r"))
    options = CompositionOptions(automatic=True, partitions={identity: (1, 3)}, pages={identity: 1})
    match target:
        case "v2":
            result = sl.planning.plan(node, target=V2, composition=options)
        case "slack":
            result = sl.planning.plan(node, target=SLACK, composition=options)
        case "html":
            result = sl.planning.plan(node, target=sl.html.target(), composition=options)
    assert result.report.search is not None and result.report.search.optimal
    assert result.allocation is not None
    assert result.allocation.regions[identity].cuts == (1, 3)
    assert result.allocation.regions[identity].anchors[0] != result.allocation.regions[identity].anchors[1]
    assert "Row 1" in scene.Codec.dumps(result.scene)
    first = result.for_page({identity: 0})
    assert "Row 0" in scene.Codec.dumps(first.scene)
    assert "Row 1" not in scene.Codec.dumps(first.scene)
    assert first.allocation is result.allocation
    assert first.report.search is result.report.search
    assert first.metrics == result.metrics
    assert result.session_updates == first.session_updates == ()


def test_default_allowance_returns_a_certified_larger_layout() -> None:
    node = sl.region(*(sl.paragraph(f"{i:02d}" + "x" * 78, key=str(i)) for i in range(30)), key="r", title="R")
    result = sl.planning.plan(
        node, target=replace(V2, limits=V2Limits(total_text=330)), composition=CompositionOptions(automatic=True)
    )
    assert result.report.search is not None
    assert result.report.search.work_used <= CompositionOptions().work_limit
    assert result.allocation is not None
    identity = OccurrenceId(("root", "r"))
    count = len(result.allocation.regions[identity].cuts)
    assert count >= 8
    for page in range(count):
        assert result.for_page({identity: page}).cost.get(Axis.DISPLAY_TEXT) <= 330


def test_exhaustion_has_one_debug_log_and_a_structured_note(caplog: pytest.LogCaptureFixture) -> None:
    node = sl.region(*(sl.paragraph("x" * 10, key=str(i)) for i in range(8)), key="r", title="R")
    with caplog.at_level(logging.DEBUG, logger="squid_ui.planning.composition_planner"):
        result = sl.planning.plan(
            node,
            target=replace(V2, limits=V2Limits(total_text=35)),
            composition=CompositionOptions(automatic=True, work_limit=10000),
        )
    assert result.report.search is not None
    assert result.report.search.completion == SearchCompletion.EXHAUSTED
    assert not result.report.search.optimal
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert all(event.severity != sl.planning.PlanSeverity.WARNING for event in result.report.events)


def test_exhaustion_before_discovery_is_structured(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="squid_ui.planning.composition_planner"),
        pytest.raises(JointExhaustedError) as caught,
    ):
        sl.planning.plan(sl.paragraph("Body"), target=V2, composition=CompositionOptions(automatic=True, work_limit=1))
    assert caught.value.diagnostics.work_limit == 1
    assert len(caplog.records) == 1


def test_bindings_remain_page_local_and_no_state_is_published() -> None:
    async def first(event: sl.PressEvent[object]) -> None:
        pytest.fail("planning invoked a handler")

    async def second(event: sl.PressEvent[object]) -> None:
        pytest.fail("planning invoked a handler")

    node = sl.region(
        sl.action_controls(
            sl.action_control("First", handlers(FrontendHandler("joint.discord", first)), key="a"), key="first"
        ),
        sl.action_controls(
            sl.action_control("Second", handlers(FrontendHandler("joint.discord", second)), key="b"), key="second"
        ),
        key="r",
        title="R",
    )
    identity = OccurrenceId(("root", "r"))
    session = PresentationState()
    result = sl.planning.plan(
        node, target=V2, session=session, composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)})
    )
    assert {binding.handler for binding in result.bindings.values()} == {FrontendHandler("joint.discord", first)}
    assert {binding.handler for binding in result.for_page({identity: 1}).bindings.values()} == {
        FrontendHandler("joint.discord", second)
    }
    assert session.revision == 0
    assert not result.session_updates


def test_reservations_and_custom_chrome_are_certified() -> None:
    node = sl.region(sl.paragraph("aaaa"), sl.paragraph("bbbb"), key="r", title="R")
    identity = OccurrenceId(("root", "r"))
    result = sl.planning.plan(
        node,
        target=V2,
        reservation=ResourceCost({Axis.COMPONENTS: 30}),
        chrome=replace(Chrome(), page_footer=lambda page, pages: f"[{page}/{pages}]"),
        composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)}),
    )
    assert "[1/2]" in scene.Codec.dumps(result.scene)
    assert result.cost.get(Axis.COMPONENTS) <= 10


def test_custom_automatic_rules_share_the_public_boundary() -> None:
    result = sl.planning.plan(
        sl.paragraph("Body"),
        target=V2,
        composition=AutomaticComposition(V2CompositionRules[ComponentsV2Target, object]()),
    )
    assert result.allocation is not None
    assert result.report.search is not None and result.report.search.optimal
    assert result.metrics.target_evaluations == 1


def test_unsupported_automatic_target_has_structured_capability_diagnostics() -> None:
    target = classic_target(AdapterProfile(DiscordAdapter, "joint.classic", ">=1"))
    with pytest.raises(JointUnsupportedError) as caught:
        sl.planning.plan(sl.paragraph("Body"), target=target, composition=CompositionOptions(automatic=True))
    assert caught.value.diagnostics.completion == SearchCompletion.UNSUPPORTED
    assert caught.value.diagnostics.violations
