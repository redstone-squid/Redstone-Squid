"""Destination ownership stays independent of layout page coordinates."""

from dataclasses import replace
from typing import Any
from unittest.mock import Mock

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.interactions import ActionResponder, Actor, PressEvent, SelectionEvent
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.semantic import NavigationDisplay
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "navigation.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "navigation.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("display", [NavigationDisplay.INDIVIDUAL, NavigationDisplay.GROUPED])
async def test_destination_callbacks_preserve_controlled_ownership(
    target: Target[Any, Any, Any, Any], display: NavigationDisplay
) -> None:
    received: list[str] = []

    async def changed(event: sl.NavigateEvent) -> None:
        received.append(event.destination)

    session = PresentationState()
    node = sl.navigation(
        sl.nav_option("First", key="a"),
        sl.nav_option("Second", key="b"),
        sl.nav_option("Unavailable", key="c", available=False),
        key="destinations",
        current=sl.controlled("a", changed),
        display=display,
    )
    result = plan(node, target=target, session=session)
    assert result.allocation is not None
    assert "Unavailable" not in scene.Codec.dumps(result.scene)
    assert not received and not session.selections
    if display == NavigationDisplay.INDIVIDUAL:
        await result.bindings["destinations.b"].handler(
            PressEvent(Actor("reader"), Mock(spec=ActionResponder), context=None)
        )
        if isinstance(result.scene.body, scene.HtmlBody):
            nav = result.scene.body.children[0]
            assert isinstance(nav, scene.HtmlElement) and nav.tag == scene.HtmlTag.NAV
            current = nav.children[0]
            assert isinstance(current, scene.HtmlElement)
            assert scene.HtmlAttribute(scene.HtmlAttributeName.ARIA_CURRENT, "page") in current.attributes
    else:
        binding = next(iter(result.bindings.values()))
        await binding.handler(SelectionEvent(Actor("reader"), Mock(spec=ActionResponder), values=("b",), context=None))
    assert received == ["b"]
    assert not session.selections


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_navigation_selection_changes_invalidate_retained_allocations(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.navigation(sl.nav_option("First", key="a"), sl.nav_option("Second", key="b"), key="destinations")
    first = plan(node, target=target, session=session)
    session.apply_updates(first.session_updates)
    session.select("destinations", ("b",))
    second = plan(node, target=target, session=session)
    assert second.allocation is not first.allocation
    assert second.scene != first.scene


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_destination_pages_are_independent_of_content_pages(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.group(
        sl.region(sl.paragraph("first body", key="a"), sl.paragraph("second body", key="b"), key="r", title="R"),
        sl.navigation(
            *(sl.nav_option(f"Destination {index}", key=str(index)) for index in range(4)),
            key="destinations",
            display=NavigationDisplay.GROUPED,
        ),
        key="body",
    )
    outer, navigation = OccurrenceId(("root", "body", "r")), OccurrenceId(("root", "body", "destinations"))
    options = CompositionOptions(automatic=True, partitions={outer: (1, 2), navigation: (2, 4)})
    first = plan(node, target=target, composition=options, session=session)
    assert first.allocation is not None
    for body_page in range(2):
        for nav_page in range(2):
            wire = scene.Codec.dumps(first.allocation.select({outer: body_page, navigation: nav_page}).scene)
            assert ("first body" if body_page == 0 else "second body") in wire
            for index in range(4):
                assert (f"Destination {index}" in wire) == (index // 2 == nav_page)
    assert not session.selections


@pytest.mark.parametrize("target", [*TARGETS[:2], *TARGETS[3:]], ids=lambda target: target.id)
def test_grouped_navigation_paginates_at_the_native_option_limit(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.navigation(
        *(sl.nav_option(f"Destination {index}", key=str(index)) for index in range(5)),
        key="destinations",
        display=NavigationDisplay.GROUPED,
    )
    limited = replace(
        target, limits=replace(target.limits, components=replace(target.limits.components, select_options=2))
    )
    result = plan(node, target=limited)
    assert result.allocation is not None
    assert result.scene.pagers[0].pages == 3
    assert "__cursor_next.destinations.destinations" in result.bindings


def test_alternatives_cannot_change_destination_ownership_without_permission() -> None:
    async def first(event: sl.NavigateEvent) -> None: ...
    async def second(event: sl.NavigateEvent) -> None: ...

    source = sl.navigation(sl.nav_option("First", key="a"), key="destinations", current=sl.controlled("a", first))
    changed = replace(source, current=sl.controlled("a", second))
    node = sl.alternatives(sl.alternative(changed, key="changed", fidelity=sl.equivalent()), content=source, key="alt")
    with pytest.raises(LayoutInvariantError, match="interaction change"):
        plan(node, target=TARGETS[0], composition=CompositionOptions(automatic=True))
