"""Tabs: one strip, one selected body, and the shape the strip takes as it grows."""

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import Choices
from squid_ui_widgets import testing as wt


class Screen(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return sl.paragraph(f"screen: {self.name}")


def _settings() -> sp.Tabs:
    return sp.Tabs(
        [
            sp.Tab("general", "General", sl.paragraph("general body")),
            sp.Tab("privacy", "Privacy", sl.paragraph("privacy body")),
        ],
        key="settings",
        title="Settings",
    )


async def test_selecting_a_tab_swaps_the_body_and_nothing_else() -> None:
    harness = wt.mounted(_settings())

    assert "general body" in harness.texts()
    assert "privacy body" not in harness.texts()

    await harness.press("settings.privacy")

    assert harness.state == sp.TabsState("privacy")
    assert "privacy body" in harness.texts()
    assert "general body" not in harness.texts()


async def test_a_strip_too_wide_for_buttons_becomes_one_picker_over_every_tab() -> None:
    """Six tabs is past the button budget, so the strip is a single `Choices` -- and it still
    carries every tab, rather than dropping the ones that did not fit."""
    many = sp.Tabs([sp.Tab(str(index), f"Tab {index}", sl.paragraph(str(index))) for index in range(6)], key="many")
    harness = wt.mounted(many)

    picker = engine.find(harness.nodes, Choices, key="many")

    assert len(picker.choices) == 6
    assert [choice.key for choice in picker.choices] == [str(index) for index in range(6)]

    await harness.choose("many", "4")

    assert harness.state == sp.TabsState("4")


async def test_only_the_selected_tab_embeds_its_component() -> None:
    """An unselected tab's component must not render: it may be expensive, and it is not on
    screen. The keys of the strip still name every tab."""
    harness = wt.mounted(
        sp.Tabs([sp.Tab("one", "One", Screen("one")), sp.Tab("two", "Two", Screen("two"))], key="screens")
    )

    assert "screen: one" in harness.texts()
    assert "screen: two" not in harness.texts()
    assert {control.key for control in engine.find_all(harness.nodes, sl.semantic.ActionControl)} == {
        "screens.one",
        "screens.two",
    }


def test_the_router_shell_encodes_the_next_state_for_a_small_strip() -> None:
    small = sp.Tabs([sp.Tab("one", "One", "one"), sp.Tab("two", "Two", "two")], key="tabs")

    render = wt.routed(small)

    assert render.route_for("select:two") == sp.TransitionRoute("select:two", sp.TabsState("two"), "next")
    assert len(render.route_ids()) == 2


def test_the_router_shell_encodes_the_current_state_when_the_strip_takes_input() -> None:
    """A wide strip is one picker, so the id must carry the state the selection applies *to*,
    not the state it will produce -- the value arrives with the interaction."""
    many = sp.Tabs([sp.Tab(str(index), str(index), str(index)) for index in range(6)], key="many")

    render = wt.routed(many)

    assert render.route_for("select") == sp.TransitionRoute("select", sp.TabsState("0"), "input")
