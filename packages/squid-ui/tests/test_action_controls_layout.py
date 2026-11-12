"""How `action_controls` lays out actions, links, toggles, and forms on each target.

Before toggles and forms could join `action_controls`, each one took a row of its own, and links
moved behind the actions. These tests pin the order and row sharing that replaced that.
"""

from typing import Any

import pytest

import squid_ui as sl
from squid_ui import forms, scene
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.runtime.component import render_component_tree
from squid_ui.semantic import ControlDisplay
from squid_ui.target_types import DiscordAdapter, SlackAdapter
from squid_ui.testing import keys

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "tests.actions", ">=1"))
CLASSIC = classic_target(AdapterProfile(DiscordAdapter, "tests.actions", ">=1"))
SLACK = sl.slack.message_target(adapter=AdapterProfile(SlackAdapter, "tests.actions", ">=1"))
FORM = forms.FormSpec("Edit page", (forms.TextField("Name", "name"),))


async def _pressed(_event: ActionEvent) -> None: ...


async def _submitted(_event: SubmitEvent) -> None: ...


def _mixed(**options: Any) -> sl.LayoutNode:
    return sl.action_controls(
        sl.action_control("Save", _pressed, key="save"),
        sl.toggle("Alerts", key="alerts"),
        sl.form("Edit", FORM, key="edit", on_submit=_submitted),
        sl.link("Docs", "https://example.invalid/docs", key="docs"),
        key="actions",
        **options,
    )


def _v2_rows(node: sl.LayoutNode) -> list[list[str | None]]:
    body = sl.planning.plan(node, target=V2).scene.body
    assert isinstance(body, scene.ComponentsV2)
    return [_labels(child.items) for child in body.children if isinstance(child, scene.Row)]


def _labels(items: tuple[object, ...]) -> list[str | None]:
    return [getattr(item, "label", None) for item in items]


def test_mixed_controls_share_one_row_in_author_order() -> None:
    assert _v2_rows(_mixed()) == [["Save", "Alerts: Off", "Edit", "Docs"]]


def test_classic_messages_share_the_row_too() -> None:
    body = sl.planning.plan(_mixed(), target=CLASSIC).scene.body
    assert isinstance(body, scene.ClassicMessage)
    assert [_labels(row.controls) for row in body.rows] == [["Save", "Alerts: Off", "Edit", "Docs"]]


def test_nested_toggles_and_forms_keep_their_bindings() -> None:
    result = sl.planning.plan(_mixed(), target=V2)

    assert {"save", "alerts", "edit"} <= set(result.bindings)
    assert set(result.form_bindings) == {"edit"}


def test_a_control_group_starts_its_own_row() -> None:
    rows = _v2_rows(
        sl.action_controls(
            sl.action_control("Back", _pressed, key="back"),
            sl.control_group(
                sl.action_control("Undo", _pressed, key="undo"),
                sl.action_control("Redo", _pressed, key="redo"),
                key="history",
            ),
            sl.toggle("Alerts", key="alerts"),
            key="actions",
        )
    )

    assert rows == [["Back"], ["Undo", "Redo"], ["Alerts: Off"]]


def test_more_than_five_buttons_wrap_onto_a_second_row() -> None:
    rows = _v2_rows(
        sl.action_controls(
            *(sl.action_control(f"A{index}", _pressed, key=f"a{index}") for index in range(4)),
            sl.toggle("Alerts", key="alerts"),
            sl.form("Edit", FORM, key="edit", on_submit=_submitted),
            key="actions",
        )
    )

    assert rows == [["A0", "A1", "A2", "A3", "Alerts: Off"], ["Edit"]]


def test_folding_into_a_menu_keeps_toggles_and_forms_as_buttons() -> None:
    body = sl.planning.plan(_mixed(display=ControlDisplay.GROUPED), target=V2).scene.body
    assert isinstance(body, scene.ComponentsV2)

    menus = [child for child in body.children if isinstance(child, scene.Select)]
    rows = [_labels(child.items) for child in body.children if isinstance(child, scene.Row)]
    assert [[option.label for option in menu.options] for menu in menus] == [["Save"]]
    assert rows == [["Alerts: Off", "Edit", "Docs"]]


def test_html_places_the_toggle_and_form_inside_the_actions() -> None:
    planned = sl.planning.plan(_mixed(), target=sl.html.target())
    html = sl.html.Renderer().draw(planned.scene, plan=planned)

    nav = html[html.index("<nav") : html.index("</nav>")]
    assert 'type="checkbox"' in nav
    assert "<form" in nav
    assert nav.index("Save") < nav.index("Alerts") < nav.index("Edit") < nav.index("Docs")


def test_slack_shows_one_button_per_control_and_no_form_fields() -> None:
    result = sl.planning.plan(_mixed(), target=SLACK)

    actions = [block for block in result.scene.body.blocks if isinstance(block, scene.SlackActions)]
    assert len(actions) == 1
    buttons = [element for element in actions[0].elements if isinstance(element, scene.SlackButton)]
    assert len(buttons) == len(actions[0].elements)
    assert [button.label.content for button in buttons] == ["Save", "Alerts", "Edit", "Docs"]


@pytest.mark.parametrize("entry", [sl.toggle("Alerts", key="alerts"), sl.control_group(key="empty")])
def test_the_factory_accepts_toggles_and_control_groups(entry: Any) -> None:
    assert sl.action_controls(entry, key="actions").items == (entry,)


class _Preferences(sl.Component):
    def render(self) -> sl.LayoutNode:
        return sl.action_controls(
            sl.toggle("Alerts", key="alerts"),
            sl.form("Edit", FORM, key="edit", on_submit=_submitted),
            key="actions",
        )


class _TwoPreferences(sl.Component):
    def __init__(self) -> None:
        self.first, self.second = _Preferences(), _Preferences()

    def render(self) -> sl.LayoutNode:
        return sl.block(self.first.keyed("first"), self.second.keyed("second"))


def test_embedded_components_scope_their_nested_toggle_and_form_keys() -> None:
    tree = render_component_tree(_TwoPreferences())

    nested = [key for key in keys(tree.nodes) if key.endswith((".alerts", ".edit"))]
    assert len(nested) == len(set(nested)) == 4
