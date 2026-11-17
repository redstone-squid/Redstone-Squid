"""Static line collections preserve complete entries unless their policy allows loss."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.limits import ClassicLimits, V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.primitives import nodes as native
from squid_ui.ext.discord.target_types import DiscordAdapter, DiscordTarget
from squid_ui.overflow import Alt, Condense, Drop, Never, Paginate, Truncate, alts
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.semantic_correspondence import check_alternative
from squid_ui.planning.target import Target

ADAPTER = AdapterProfile(DiscordAdapter, "native-lines.discord", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(ADAPTER, limits=V2Limits(total_text=10)),
    classic_target(ADAPTER, limits=ClassicLimits(content=10)),
]
AUTOMATIC = CompositionOptions(automatic=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_spill_reports_whole_omitted_entries_and_reserves_its_notice(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(("first", "second", "third"), keys=("one", "two", "three"))
    result = plan(node, target=target, composition=AUTOMATIC, chrome=Chrome(and_n_more=lambda count: f"+{count}"))
    assert "first\\n+2" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Omission", "Omission"]
    assert {event.path.split("/")[-1] for event in result.report.events} == {"two", "three"}
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=AUTOMATIC, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_lines_never_cannot_silently_shorten_entries(target: Target[Any, Any, Any, Any]) -> None:
    with pytest.raises(JointInfeasibleError):
        plan(native.Lines(("first", "second"), overflow=Never()), target=target, composition=AUTOMATIC)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_line_truncation_keeps_joiner_and_requested_tail(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(("abcdefgh", "ijklmnop"), join="/", overflow=Truncate("tail"), keys=("first", "last"))
    result = plan(node, target=target, composition=AUTOMATIC)
    assert "…/ijklmnop" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Shortening"]
    assert result.report.events[0].path.endswith("/first")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_line_drop_is_whole_collection_omission(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(native.Lines(("first", "second"), overflow=Drop()), target=target, composition=AUTOMATIC)
    wire = scene.Codec.dumps(result.scene)
    assert "first" not in wire and "second" not in wire
    assert [event.message for event in result.report.events] == ["Omission", "Omission"]


def test_region_pagination_precedes_static_spill_and_preserves_entry_order() -> None:
    entries = tuple(f"entry-{index}" for index in range(6))
    result = plan(
        sl.region(native.Lines(entries), key="r", title="R"),
        target=components_v2_target(ADAPTER, limits=V2Limits(total_text=35)),
    )
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    reconstructed: list[str] = []
    for index in range(len(result.allocation.regions[identity].cuts)):
        page = result.for_page({identity: index})
        assert not page.report.events
        reconstructed.extend(
            line
            for node in page.scene.body.children
            if isinstance(node, discord_scene.Text)
            for line in node.content.splitlines()
            if line.startswith("entry-")
        )
    assert tuple(reconstructed) == entries


@pytest.mark.parametrize(
    "target", [components_v2_target(ADAPTER), classic_target(ADAPTER)], ids=lambda target: target.id
)
def test_paginated_line_alternates_keep_primary_content_when_pages_fit(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(
        (Alt("Long entry", ("Short",)), "Second entry"), overflow=Paginate(key="lines", per=1), keys=("one", "two")
    )
    result = plan(node, target=target, strict=True)
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    assert identity == OccurrenceId(("root", "lines"))
    for index, content in enumerate(("Long entry", "Second entry")):
        page = result.for_page({identity: index})
        assert content in scene.Codec.dumps(page.scene)
        assert not page.report.events


def test_paginated_line_ladder_reports_original_identity() -> None:
    # One component leaves no room for pager controls, so only the short rung fits.
    node = native.Lines((Alt("abcdef", ("short",)),), overflow=Paginate(key="lines"), keys=("entry",))
    result = plan(
        node,
        target=components_v2_target(ADAPTER, limits=V2Limits(total_text=5, total_components=1)),
    )
    assert result.allocation is not None
    assert "short" in scene.Codec.dumps(result.scene)
    assert [(event.message, event.path.split("/")[-1]) for event in result.report.events] == [("Replacement", "entry")]


def test_equivalent_alternative_cannot_remove_a_native_line_pager() -> None:
    source = native.Lines(("one", "two"), overflow=Paginate(key="lines", per=1), keys=("one", "two"))
    choice = sl.alternative(
        replace(source, overflow=Never()),
        key="changed",
        fidelity=sl.equivalent(),
        correspondence=(sl.corresponds("one", "one"), sl.corresponds("two", "two")),
    )
    with pytest.raises(LayoutInvariantError, match="boundary"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)


def test_shortened_lines_in_checked_presentations_report_original_sources() -> None:
    source = native.Lines(("abcdefgh", "ijklmnop"), join="/", overflow=Never(), keys=("first", "last"))
    document = sl.alternatives(
        sl.alternative(replace(source, overflow=Truncate("tail")), key="shortenable", fidelity=sl.equivalent()),
        content=source,
        key="choice",
    )
    result = plan(document, target=TARGETS[0])
    assert "…/ijklmnop" in scene.Codec.dumps(result.scene)
    assert [event.path.split("/")[-1] for event in result.report.events] == ["first"]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_line_ladders_step_the_largest_eligible_entry_first(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines((Alt("abcdefgh", ("a",)), Alt("0123456789", ("0",))), keys=("first", "second"))
    result = plan(node, target=target, composition=AUTOMATIC)
    assert "abcdefgh\\n0" in scene.Codec.dumps(result.scene)
    assert [(event.message, event.path.split("/")[-1]) for event in result.report.events] == [("Replacement", "second")]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_spill_preserves_priority_and_does_not_report_hidden_replacements(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(
        (Alt("low long", ("low",), priority=-5), Alt("HIGH", priority=10), "TAIL"), keys=("low", "high", "tail")
    )
    result = plan(node, target=target, composition=AUTOMATIC, chrome=Chrome(and_n_more=lambda count: f"+{count}"))
    assert "HIGH\\n+2" in scene.Codec.dumps(result.scene)
    assert {event.message for event in result.report.events} == {"Omission"}
    assert {event.path.split("/")[-1] for event in result.report.events} == {"low", "tail"}


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_condense_exhausts_ladders_before_shortening_the_joined_text(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines((Alt("Long primary", ("123456",)), "abcdef"), overflow=Condense(), keys=("first", "second"))
    result = plan(node, target=target, composition=AUTOMATIC)
    assert "123456\\nab…" in scene.Codec.dumps(result.scene)
    assert {(event.message, event.path.split("/")[-1]) for event in result.report.events} == {
        ("Replacement", "first"),
        ("Shortening", "second"),
    }
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=AUTOMATIC, strict=True)


def test_never_does_not_activate_line_replacement_ladders() -> None:
    node = native.Lines((Alt("Long primary", ("Short",)),), overflow=Never())
    with pytest.raises(JointInfeasibleError):
        plan(node, target=TARGETS[0], composition=AUTOMATIC)


def test_line_replacement_does_not_outrank_lossless_region_pages() -> None:
    node = native.Lines((Alt("a" * 25, ("A",)), Alt("b" * 25, ("B",))))
    result = plan(
        sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER, limits=V2Limits(total_text=40))
    )
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    assert len(result.allocation.regions[identity].cuts) == 2
    for index, expected in enumerate(("a" * 25, "b" * 25)):
        page = result.for_page({identity: index})
        assert expected in scene.Codec.dumps(page.scene)
        assert not page.report.events


@pytest.mark.parametrize(
    "target", [components_v2_target(ADAPTER), classic_target(ADAPTER)], ids=lambda target: target.id
)
def test_unkeyed_native_pagers_have_independent_positional_cursors(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(
        sl.stack(
            native.Lines(("one", "two"), overflow=Paginate(per=1)),
            native.Text("alpha\nbeta", overflow=Paginate(per=1)),
        ),
        target=target,
    )
    assert result.allocation is not None
    regions = tuple(result.allocation.regions.items())
    assert len(regions) == 2
    assert regions[0][1].cursor_key != regions[1][1].cursor_key
    assert all(region.cursor_key.startswith("$/") for _, region in regions)
    page = result.for_page({regions[0][0]: 1, regions[1][0]: 0})
    wire = scene.Codec.dumps(page.scene)
    assert "two" in wire and "alpha" in wire
    assert '"one' not in wire and "beta" not in wire


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_collection_replacement_has_one_source_without_inventing_line_correspondence(
    target: Target[Any, Any, Any, Any],
) -> None:
    node = native.Lines(("long first entry", "long second entry"), overflow=alts("replacement"))
    result = plan(node, target=target)
    assert result.allocation is not None
    assert "replaceme…" in scene.Codec.dumps(result.scene)
    assert {event.message for event in result.report.events} == {"Replacement", "Shortening"}
    assert len({event.path for event in result.report.events}) == 1
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


def test_collection_replacement_source_can_be_referenced_as_one_text_unit() -> None:
    source = native.Lines(("one", "two"), overflow=alts("short"))
    choice = sl.Alternative[DiscordTarget](
        sl.present_text(sl.ref("@0"), key="copy"), key="copy", fidelity=sl.equivalent()
    )
    node = sl.alternatives(
        choice,
        content=source,
        key="collection",
    )
    result = plan(node, target=TARGETS[0], strict=True)
    assert "one\\ntwo" in scene.Codec.dumps(result.scene)
    assert not result.report.events
