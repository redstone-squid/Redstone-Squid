"""The shared two-shell rule and the first three pattern specifications."""

from dataclasses import dataclass

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Mount
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.primitives import Lines
from squid_layouts.scene import SceneRoutedButton, SceneRoutedSelect
from squid_layouts.semantic import Stack


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def _labels(view: discord.ui.LayoutView) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]


class Screen(sl.Component):
    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return sl.paragraph(f"screen: {self.name}")


async def test_tabs_component_shell_switches_content_and_adapts_large_strips() -> None:
    pattern = sl.Tabs(
        [
            sl.Tab("general", "General", sl.paragraph("general body")),
            sl.Tab("privacy", "Privacy", sl.paragraph("privacy body")),
        ],
        key="settings",
        heading="Settings",
    )
    tabs = pattern.component()
    mount = Mount(tabs, timeout=None)

    view = commit_render(mount)
    assert "general body" in _texts(view)
    assert "privacy body" not in _texts(view)

    await mount.dispatch("settings.privacy", fake_interaction())
    view = commit_render(mount)
    assert tabs.pattern_state == sl.TabsState("privacy")
    assert "privacy body" in _texts(view)
    assert "general body" not in _texts(view)

    many_pattern = sl.Tabs(
        [sl.Tab(str(index), f"Tab {index}", sl.paragraph(str(index))) for index in range(6)],
        key="many-tabs",
    )
    many = many_pattern.component()
    many_mount = Mount(many, timeout=None)
    many_view = commit_render(many_mount)
    select = next(item for item in many_view.walk_children() if isinstance(item, discord.ui.Select))
    assert select.custom_id is not None and select.custom_id.endswith(":many-tabs")
    assert len(select.options) == 6

    await many_mount.dispatch("many-tabs", fake_interaction(), ["4"])
    assert many.pattern_state == sl.TabsState("4")


async def test_tabs_component_shell_embeds_only_selected_content() -> None:
    tabs = sl.Tabs(
        [sl.Tab("one", "One", Screen("one")), sl.Tab("two", "Two", Screen("two"))],
        key="screens",
    ).component()
    mount = Mount(tabs, timeout=None)

    view = commit_render(mount)
    assert "screen: one" in _texts(view)
    assert "screen: two" not in _texts(view)
    assert set(mount._handlers) == {"screens.one", "screens.two"}


def test_tabs_router_shell_encodes_next_state_and_input_state() -> None:
    small = sl.Tabs(
        [sl.Tab("one", "One", "one"), sl.Tab("two", "Two", "two")],
        key="tabs",
    )
    routes: list[sl.PatternRoute[sl.TabsState]] = []

    def route(request: sl.PatternRoute[sl.TabsState]) -> str:
        routes.append(request)
        return f"tabs:{request.state.selected}"

    rendered = sl.RouterShell(route).render(small, small.initial_state)
    scene = sl.plan(rendered, target=sl.discord.DEFAULT_TARGET).scene
    buttons = [item for row in scene.children if hasattr(row, "items") for item in row.items]
    assert all(isinstance(item, SceneRoutedButton) for item in buttons)
    assert routes[-1] == sl.PatternRoute("select:two", sl.TabsState("two"), "next")

    many = sl.Tabs([sl.Tab(str(index), str(index), str(index)) for index in range(6)], key="many")
    routed = sl.RouterShell(route).render(many, many.initial_state)
    routed_scene = sl.plan(routed, target=sl.discord.DEFAULT_TARGET).scene
    assert isinstance(routed_scene.children[0], SceneRoutedSelect)
    assert routes[-1] == sl.PatternRoute("select", sl.TabsState("0"), "input")


async def test_menu_component_shell_drills_down_and_owns_chrome() -> None:
    menu = sl.Menu(
        "Settings",
        [
            sl.MenuEntry("appearance", "Appearance", sl.paragraph("appearance body")),
            sl.MenuEntry(
                "Administration",
                Screen("administration"),
                entries=[sl.MenuEntry("Audit", sl.paragraph("audit body"), key="audit")],
            ),
        ],
        key="settings",
    ).component()
    mount = Mount(menu, timeout=None)

    view = commit_render(mount)
    assert _texts(view) == ["## Settings"]
    assert _labels(view)[-3:] == ["Back", "Home", "Close"]

    await mount.dispatch("settings.administration", fake_interaction())
    view = commit_render(mount)
    assert menu.pattern_state == sl.MenuState(("administration",))
    assert "screen: administration" in _texts(view)
    assert "Audit" in _labels(view)

    await mount.dispatch("settings.audit", fake_interaction())
    assert menu.pattern_state == sl.MenuState(("administration", "audit"))
    await mount.dispatch("settings.home", fake_interaction())
    assert menu.pattern_state == sl.MenuState()
    await mount.dispatch("settings.close", fake_interaction())
    assert mount._finished


def test_menu_entry_supports_shorthand_keys_and_rejects_duplicates() -> None:
    shorthand = sl.MenuEntry("Appearance", sl.paragraph("body"))
    assert shorthand.key == "appearance"

    with pytest.raises(ValueError, match="keys must be unique"):
        sl.Menu(
            "Settings",
            [sl.MenuEntry("Same", sl.paragraph("one")), sl.MenuEntry("Same", sl.paragraph("two"))],
        )


@dataclass(frozen=True)
class Score:
    name: str
    points: int


def test_ranked_list_projects_entries_and_renders_an_explicit_window() -> None:
    ranked = sl.RankedList(
        [Score("Ada", 30), Score("Grace", 20), Score("Edsger", 10)],
        key="leaderboard",
        label="name",
        value=lambda entry: entry.points,
        heading="Leaderboard",
        header=lambda total: sl.paragraph(f"Showing {total} entries"),
        footer=lambda total: sl.note(f"Total: {total}"),
        page_size=2,
    ).component()
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, Lines))
    assert listing.lines == ("1. **Ada** — 30", "2. **Grace** — 20")

    view = commit_render(Mount(ranked, timeout=None))
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(view)
    assert "Showing 3 entries" in _texts(view)
    assert "-# Page 1 of 2" in _texts(view)


async def test_ranked_list_keeps_global_ranks_on_later_pages() -> None:
    ranked = sl.RankedList([("Ada", 30), ("Grace", 20), ("Edsger", 10)], key="leaderboard", page_size=2).component()
    mount = Mount(ranked, timeout=None)
    commit_render(mount)

    await mount.dispatch("leaderboard.next", fake_interaction())
    view = commit_render(mount)
    assert ranked.pattern_state == sl.RankedListState(1)
    assert "3. **Edsger** — 10" in _texts(view)


def test_ranked_list_top_n_and_explicit_entries() -> None:
    ranked = sl.RankedList(
        [
            sl.RankedEntry("Ada", 30, key="ada"),
            sl.RankedEntry("Grace", 20, key="grace"),
            sl.RankedEntry("Edsger", 10, key="edsger"),
        ],
        key="top",
        top_n=2,
    ).component()
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, Lines))
    assert listing.lines == ("1. **Ada** — 30", "2. **Grace** — 20")


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"limit": 0}, {"page_size": 0}])
def test_ranked_list_rejects_non_positive_limits(kwargs) -> None:
    with pytest.raises(ValueError):
        sl.RankedList([], key="ranked", **kwargs)
