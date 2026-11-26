"""Item selection and layout pagination retain independent state and visible bindings."""

from typing import Any
from unittest.mock import Mock

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.authoring.semantic import ItemDisplay
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.interactions import ActionResponder, Actor, PressEvent, SelectionEvent
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.presentation_state import PresentationState

DISCORD = AdapterProfile(DiscordAdapter, "items.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "items.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning invoked item content")


def catalogue() -> sl.semantic.Items:
    return sl.items(
        *(
            sl.item(
                sl.item_label(f"Item {key}"),
                sl.paragraph(f"Detail {key}"),
                sl.action_controls(
                    sl.action_control(f"Action {key}", untouched, key=f"action-{key}"), key=f"actions-{key}"
                ),
                key=key,
                summary=f"Summary {key}",
            )
            for key in ("a", "b")
        ),
        key="catalog",
    )


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_opening_and_closing_an_item_restores_its_overview_page(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = catalogue()
    options = CompositionOptions(automatic=True, partitions={OccurrenceId(("root", "catalog")): (1, 2)})
    overview = plan(
        node, target=target, composition=options, session=session, positions={"catalog.items": PagePosition(page=1)}
    )
    assert not session.node_values and not session.cursors
    assert "catalog.focus" in overview.bindings
    assert "__cursor_previous.catalog.items" in overview.bindings
    assert "action-a" not in overview.bindings and "action-b" not in overview.bindings
    assert "Summary b" in scene.Codec.dumps(overview.scene)
    assert "Summary a" not in scene.Codec.dumps(overview.scene)
    session.apply_updates(overview.session_updates)
    remembered = session.cursor("catalog.items")
    await overview.bindings["catalog.focus"].handler(
        SelectionEvent(Actor("reader"), Mock(spec=ActionResponder), values=("b",), context=None)
    )
    opened = plan(node, target=target, session=session)
    assert "action-b" in opened.bindings and "action-a" not in opened.bindings
    assert "catalog.back" in opened.bindings and "catalog.focus" not in opened.bindings
    assert "Detail b" in scene.Codec.dumps(opened.scene)
    session.apply_updates(opened.session_updates)
    assert session.cursor("catalog.items") == remembered
    await opened.bindings["catalog.back"].handler(PressEvent(Actor("reader"), Mock(spec=ActionResponder), context=None))
    returned = plan(node, target=target, composition=options, session=session)
    assert returned.scene.pagers[0].page == 1
    assert "Summary b" in scene.Codec.dumps(returned.scene)
    assert "action-b" not in returned.bindings


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_controlled_item_selection_delegates_without_publishing_a_new_selection(
    target: Target[Any, Any, Any, Any],
) -> None:
    changes: list[str | None] = []

    async def changed(event: sl.OpenEvent[str | None]) -> None:
        changes.append(event.opened)

    session = PresentationState()
    node = sl.items(
        sl.item(sl.item_label("First"), sl.paragraph("Body"), key="a"),
        key="catalog",
        opened=None,
        on_change=changed,
    )
    result = plan(node, target=target, session=session)
    await result.bindings["catalog.focus"].handler(
        SelectionEvent(Actor("reader"), Mock(spec=ActionResponder), values=("a",), context=None)
    )
    assert changes == ["a"] and not session.node_values


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_initial_opened_display_does_not_override_a_later_back_action(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.items(
        sl.item(sl.item_label("First"), sl.paragraph("Body"), key="a"), key="catalog", display=ItemDisplay.OPENED
    )
    first = plan(node, target=target, session=session)
    assert "catalog.back" in first.bindings and not session.node_values
    await first.bindings["catalog.back"].handler(PressEvent(Actor("reader"), Mock(spec=ActionResponder), context=None))
    second = plan(node, target=target, session=session)
    assert "catalog.focus" in second.bindings


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_opened_item_heading_and_back_repeat_on_every_detail_page(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.items(
        sl.item(
            sl.item_label("First"),
            sl.paragraph("first body", key="a"),
            sl.paragraph("second body", key="b"),
            key="entry",
        ),
        key="catalog",
        opened="entry",
    )
    identity = OccurrenceId(("root", "catalog"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)}))
    assert result.allocation is not None
    for index in range(2):
        page = result.allocation.select({identity: index})
        wire = scene.Codec.dumps(page.scene)
        assert "First" in wire and ("first body" if index == 0 else "second body") in wire
        assert "catalog.back" in page.bindings


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_removed_open_item_returns_to_the_remaining_overview(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    session.set_node_value("catalog", "removed")
    result = plan(catalogue(), target=target, session=session)
    assert "catalog.focus" in result.bindings
    assert "catalog.back" not in result.bindings


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_switching_items_retains_hidden_nested_page_positions(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.items(
        *(
            sl.item(
                sl.item_label(key), sl.bullets("first body", "second body", key=f"{key}-pages", page_size=1), key=key
            )
            for key in ("a", "b")
        ),
        key="catalog",
    )
    session.set_node_value("catalog", "a")
    first = plan(node, target=target, session=session, positions={"a-pages": PagePosition(page=1)})
    session.apply_updates(first.session_updates)
    remembered = session.cursor("a-pages")
    session.set_node_value("catalog", "b")
    second = plan(node, target=target, session=session)
    session.apply_updates(second.session_updates)
    assert session.cursor("a-pages") == remembered
    assert all(pager.key != "a-pages" for pager in second.scene.pagers)
    session.set_node_value("catalog", "a")
    restored = plan(node, target=target, session=session)
    assert next(pager for pager in restored.scene.pagers if pager.key == "a-pages").page == 1
