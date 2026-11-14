"""Certified navigation observes ownership and commits only delivered positions."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.navigation import default_nav, page_select_nav
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "test", ">=1"))
REGION = OccurrenceId(("root", "r"))
OPTIONS = CompositionOptions(automatic=True, partitions={REGION: (2, 4)})


def content() -> sl.semantic.Region:
    return sl.region(*(sl.paragraph(key, key=key) for key in "abcd"), key="r", title="R")


def test_navigation_is_certified_and_cursor_only_selection_reuses_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = PresentationState()
    document = content()
    result = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert len(result.bindings) == 3
    assert any(isinstance(node, scene.Select) for node in result.scene.components_v2.children)
    session.apply_updates(result.session_updates)
    allocation = result.allocation
    committed = session.cursor("$/r")
    session.request_cursor("$/r", PagePosition(page=1))

    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a cursor turn reran allocation search")

    monkeypatch.setattr("squid_ui.planning.composition_planner.search_composition", fail)
    next_page = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert next_page.allocation is allocation
    assert next_page.scene.pagers[0].page == 1
    assert session.cursor("$/r") == committed
    retry = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert retry.scene == next_page.scene
    session.apply_updates(retry.session_updates)
    assert session.cursor("$/r").position == PagePosition("c", 1)
    assert not session.pending_positions


def test_deleted_anchor_recovers_after_repartition_and_stages_history() -> None:
    session = PresentationState()
    first = plan(
        content(),
        target=V2,
        session=session,
        nav=default_nav,
        positions={"$/r": PagePosition(page=1)},
        composition=OPTIONS,
    )
    session.apply_updates(first.session_updates)
    updated = sl.region(*(sl.paragraph(key, key=key) for key in "adb"), key="r", title="R")
    second = plan(
        updated,
        target=V2,
        session=session,
        nav=default_nav,
        composition=CompositionOptions(automatic=True, partitions={REGION: (2, 3)}),
    )
    assert second.report.regions[0].anchor == "d"
    assert second.scene.pagers[0].page == 0
    assert session.cursor("$/r").position.anchor == "c"
    session.apply_updates(second.session_updates)
    assert session.cursor("$/r").position == PagePosition("d", 0)
    assert session.cursor("$/r").source_order == ("a", "d", "b")


async def test_controlled_negative_page_dispatches_without_cursor_writes() -> None:
    selected: list[int] = []

    async def change(event: sl.PageEvent) -> None:
        selected.append(event.page)

    session = PresentationState()
    node = replace(content(), page=sl.controlled(-1, change))
    result = plan(node, target=V2, session=session, nav=default_nav, composition=OPTIONS)
    assert result.scene.pagers[0].page == 1
    session.apply_updates(result.session_updates)
    assert not session.cursors
    assert selected == []


def test_custom_navigation_cost_changes_feasibility() -> None:
    target = replace(V2, limits=V2Limits(total_components=6))
    plan(content(), target=target, nav=default_nav, composition=OPTIONS)
    with pytest.raises(sl.planning.JointInfeasibleError):
        plan(content(), target=target, nav=page_select_nav, composition=OPTIONS)
