"""Tabs: one strip, one selected body, and the shape the strip takes as it grows."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_reactivity.core import transaction
from squid_ui import testing as engine
from squid_ui.semantic import Choices
from squid_ui_widgets import testing as wt


class Screen(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, name: str) -> None:
        self.name = name

    @override
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
    harness = wt.driving(_settings())

    assert "general body" in harness.texts()
    assert "privacy body" not in harness.texts()

    await harness.press("settings.privacy")

    assert harness.state == sp.TabsState("privacy")
    assert "privacy body" in harness.texts()
    assert "general body" not in harness.texts()


async def test_a_strip_too_wide_for_buttons_becomes_one_picker_over_every_tab() -> None:
    """Tabs uses a picker above five tabs; that picker must retain every destination."""
    many = sp.Tabs([sp.Tab(str(index), f"Tab {index}", sl.paragraph(str(index))) for index in range(6)], key="many")
    harness = wt.driving(many)

    picker = engine.find(harness.nodes, Choices, key="many")

    assert len(picker.choices) == 6
    assert [choice.key for choice in picker.choices] == [str(index) for index in range(6)]

    await harness.choose("many", "4")

    assert harness.state == sp.TabsState("4")


async def test_only_the_selected_tab_embeds_its_component() -> None:
    """Only the selected body should render, while the strip retains all destinations."""
    harness = wt.driving(
        sp.Tabs([sp.Tab("one", "One", Screen("one")), sp.Tab("two", "Two", Screen("two"))], key="screens")
    )

    assert "screen: one" in harness.texts()
    assert "screen: two" not in harness.texts()
    assert {control.key for control in engine.find_all(harness.nodes, sl.semantic.ActionControl)} == {
        "screens.one",
        "screens.two",
    }


def test_the_router_shell_encodes_the_next_state_for_a_small_strip() -> None:
    small = sp.machines.Tabs([sp.Tab("one", "One", "one"), sp.Tab("two", "Two", "two")], key="tabs")

    render = wt.routed(small)

    assert render.route_for("select:two") == sp.TransitionRoute("select:two", sp.TabsState("two"), "next")
    assert len(render.route_ids()) == 2


def test_the_router_shell_encodes_the_current_state_when_the_strip_takes_input() -> None:
    """The selection arrives with the interaction, so its route carries the input state."""
    many = sp.machines.Tabs([sp.Tab(str(index), str(index), str(index)) for index in range(6)], key="many")

    render = wt.routed(many)

    assert render.route_for("select") == sp.TransitionRoute("select", sp.TabsState("0"), "input")


async def test_components_keep_independent_state_and_notify_their_own_callback() -> None:
    seen: list[tuple[sp.TabsState, sp.TabsState]] = []

    async def changed(event: sp.TransitionEvent[sp.TabsState]) -> None:
        seen.append((event.previous, event.state))

    entries = (sp.Tab("one", "One", "first"), sp.Tab("two", "Two", "second"))
    first = sp.Tabs(entries, key="tabs", on_change=changed)
    second = sp.Tabs(entries, key="tabs")
    await wt.driving(first).press("tabs.two")

    assert first.machine_state == sp.TabsState("two")
    assert second.machine_state == sp.TabsState("one")
    assert seen == [(sp.TabsState("one"), sp.TabsState("two"))]


def test_explicit_state_overrides_initial_tab_without_changing_machine_default() -> None:
    tabs = sp.Tabs(
        (sp.Tab("one", "One", "first"), sp.Tab("two", "Two", "second")),
        key="tabs",
        initial="one",
        initial_state=sp.TabsState("two"),
    )
    assert wt.driving(tabs).texts() == ["second"]
    assert tabs.machine.initial_state == sp.TabsState("one")


async def test_callback_failure_rolls_back_selection_in_a_host_transaction() -> None:
    async def failed(event: sp.TransitionEvent[sp.TabsState]) -> None:
        assert event.state == sp.TabsState("two")
        raise ValueError("save failed")

    tabs = sp.Tabs((sp.Tab("one", "One", "first"), sp.Tab("two", "Two", "second")), key="tabs", on_change=failed)
    harness = wt.driving(tabs)
    with pytest.raises(ValueError, match="save failed"), transaction():
        await harness.press("tabs.two")
    assert tabs.machine_state == sp.TabsState("one")
