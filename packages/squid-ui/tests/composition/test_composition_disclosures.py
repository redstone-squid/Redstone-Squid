"""Closed content neither publishes controls nor advances its remembered pagers."""

from typing import Any
from unittest.mock import Mock

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.html import scene as html_scene
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.interactions import ActionResponder, Actor, PressEvent
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.presentation_state import PresentationState

DISCORD = AdapterProfile(DiscordAdapter, "disclosures.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "disclosures.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning called hidden content")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_disclosure_only_binds_visible_controls_and_rebuilds_on_open(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.details(
        sl.summary("More"),
        sl.action_controls(sl.action_control("Hidden action", untouched, key="hidden"), key="actions"),
        key="details",
    )
    closed = plan(node, target=target, session=session)
    assert closed.allocation is not None
    assert set(closed.bindings) == {"details.toggle"}
    assert "Hidden action" not in scene.Codec.dumps(closed.scene)
    assert not session.node_values
    session.apply_updates(closed.session_updates)
    responder = Mock(spec=ActionResponder)
    await closed.bindings["details.toggle"].handler(PressEvent(Actor("reader"), responder, context=None))
    responder.acknowledge.assert_awaited_once()
    responder.invalidate.assert_called_once()
    opened = plan(node, target=target, session=session)
    assert opened.allocation is not closed.allocation
    assert set(opened.bindings) == {"details.toggle", "hidden"}
    if isinstance(opened.scene.body, html_scene.HtmlBody):
        details = opened.scene.body.children[0]
        assert isinstance(details, html_scene.HtmlElement) and details.tag == html_scene.HtmlTag.DETAILS
        assert details.action == html_scene.HtmlActionRef("details.toggle")
        assert html_scene.HtmlAttribute(html_scene.HtmlAttributeName.OPEN, value=True) in details.attributes


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_controlled_disclosure_delegates_without_mutating_session(target: Target[Any, Any, Any, Any]) -> None:
    changed: list[bool] = []

    async def on_change(event: sl.OpenEvent[bool]) -> None:
        changed.append(event.opened)

    session = PresentationState()
    node = sl.details(
        sl.summary("More"), sl.paragraph("Body"), key="details", open=sl.controlled(value=False, on_change=on_change)
    )
    result = plan(node, target=target, session=session)
    await result.bindings["details.toggle"].handler(
        PressEvent(Actor("reader"), Mock(spec=ActionResponder), context=None)
    )
    assert changed == [True]
    assert not session.node_values


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_repeated_disclosure_toggle_stays_available_on_continuation_pages(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.details(
            sl.summary("More"),
            sl.paragraph("first body", key="a"),
            sl.paragraph("second body", key="b"),
            key="details",
            open=sl.uncontrolled(initial=True),
        ),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert result.allocation is not None
    for index in range(2):
        page = result.allocation.select({region: index})
        assert "details.toggle" in page.bindings
        assert "More" in scene.Codec.dumps(page.scene)
        assert ("first body" if index == 0 else "second body") in scene.Codec.dumps(page.scene)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_hidden_pager_retains_its_last_delivered_cursor_without_staging_new_positions(
    target: Target[Any, Any, Any, Any],
) -> None:
    session = PresentationState()
    node = sl.details(
        sl.summary("More"), sl.bullets("first body", "second body", key="items", page_size=1), key="details"
    )
    closed = plan(node, target=target, session=session)
    session.apply_updates(closed.session_updates)
    assert not session.cursors and not closed.scene.pagers
    session.set_node_value("details", value=True)
    first = plan(node, target=target, session=session, positions={"items": PagePosition(page=1)})
    assert not session.cursors
    session.apply_updates(first.session_updates)
    remembered = session.cursor("items")
    session.set_node_value("details", value=False)
    closed_again = plan(node, target=target, session=session)
    assert not closed_again.scene.pagers
    session.apply_updates(closed_again.session_updates)
    assert session.cursor("items") == remembered
    session.set_node_value("details", value=True)
    reopened = plan(node, target=target, session=session)
    assert reopened.scene.pagers[0].page == 1
    assert "second body" in scene.Codec.dumps(reopened.scene)
