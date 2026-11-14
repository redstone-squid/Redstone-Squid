"""Fixed composition is a public, explicit alternative to legacy planning."""

from dataclasses import replace
from typing import Literal

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.planning import CompositionOptions, CompositionRejectedError, PlanCache, PlanMemo
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.composition import CapacityExceeded, OccurrenceId
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentation import PresentationState
from squid_ui.presentations import budget
from squid_ui.target_types import DiscordAdapter, SlackAdapter

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "composition.discord", ">=1"))
SLACK = sl.slack.message_target(adapter=AdapterProfile(SlackAdapter, "composition.slack", ">=1"))


@pytest.mark.parametrize("target", ["v2", "slack", "html"])
def test_public_opt_in_builds_a_selected_alternative(target: Literal["v2", "slack", "html"]) -> None:
    source = sl.paragraph("Description", key="body")
    content = sl.alternatives(
        sl.alternative(source, key="original", fidelity=sl.equivalent()),
        sl.alternative(sl.paragraph("Reworded", key="body"), key="compact", fidelity=sl.equivalent()),
        content=source,
        key="help",
    )
    options = CompositionOptions({OccurrenceId(("root", "help")): "compact"})
    match target:
        case "v2":
            result = sl.planning.plan(content, target=V2, composition=options, strict=True)
        case "slack":
            result = sl.planning.plan(content, target=SLACK, composition=options, strict=True)
        case "html":
            result = sl.planning.plan(content, target=sl.html.target(), composition=options, strict=True)
    assert "Reworded" in scene.Codec.dumps(result.scene)
    assert "Description" not in scene.Codec.dumps(result.scene)
    assert scene.Codec.loads(scene.Codec.dumps(result.scene)) == result.scene
    assert result.degradation.lossless
    assert result.session_updates == ()
    assert not result.metrics.cache_hit


def test_failed_explicit_choice_is_not_a_claim_of_unsolvability() -> None:
    table = sl.table(
        sl.columns(sl.column("Column", key="column")),
        sl.table_row("a", key="a"),
        sl.table_row("b", key="b"),
        key="table",
    )
    content = budget(table, limits={Axis.BLOCKS: 1})
    path = OccurrenceId(("root", "@0", "table"))
    with pytest.raises(CompositionRejectedError) as caught:
        sl.planning.plan(content, target=SLACK, composition=CompositionOptions({path: "records"}))
    assert any(isinstance(violation, CapacityExceeded) for violation in caught.value.violations)
    assert (
        sl.planning.plan(content, target=SLACK, composition=CompositionOptions({path: "table"})).cost.get(Axis.BLOCKS)
        == 1
    )


def test_platform_reservation_remains_mandatory() -> None:
    with pytest.raises(CompositionRejectedError):
        sl.planning.plan(
            sl.paragraph("four"),
            target=replace(V2, limits=V2Limits(total_text=4)),
            reservation=ResourceCost({Axis.DISPLAY_TEXT: 1}),
            composition=CompositionOptions(),
        )


def test_unbounded_html_does_not_invent_a_character_limit() -> None:
    result = sl.planning.plan(sl.paragraph("x" * 12000), target=sl.html.target(), composition=CompositionOptions())
    assert "x" * 12000 in sl.html.Renderer().draw(result.scene, plan=result)


def test_opt_in_does_not_replay_a_cached_callback_or_publish_session_state() -> None:
    async def first(event: sl.PressEvent[object]) -> None:
        pytest.fail("planning invoked first handler")

    async def second(event: sl.PressEvent[object]) -> None:
        pytest.fail("planning invoked second handler")

    cache, memo = PlanCache[scene.HtmlBody](), PlanMemo[scene.HtmlBody]()
    session = PresentationState()
    for handler in (first, second):
        result = sl.planning.plan(
            sl.action_controls(sl.action_control("Open", handler, key="open"), key="actions"),
            target=sl.html.target(),
            composition=CompositionOptions(),
            cache=cache,
            memo=memo,
            session=session,
        )
        assert next(iter(result.bindings.values())).handler is handler
        assert not result.metrics.cache_hit and not result.session_updates
    assert session.revision == 0


def test_typo_in_a_selection_path_is_rejected() -> None:
    with pytest.raises(LayoutInvariantError, match="unknown occurrence"):
        sl.planning.plan(
            sl.paragraph("Body"), target=V2, composition=CompositionOptions({OccurrenceId(("typo",)): "text"})
        )


def test_fixed_composition_shares_the_requested_work_limit() -> None:
    with pytest.raises(LayoutSearchExhaustedError):
        sl.planning.plan(sl.paragraph("Body"), target=V2, composition=CompositionOptions(), search_budget=1)
