"""High-level Tabs, Menu, and RankedList patterns."""

from dataclasses import dataclass

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Mount
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.semantic import List, Stack


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def _labels(view: discord.ui.LayoutView) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]


class Screen(sl.Component):
    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return sl.paragraph(f"screen: {self.name}")


async def test_tabs_switch_content_and_adapt_large_strips_to_a_select() -> None:
    tabs = sl.Tabs(
        [
            sl.Tab("general", "General", sl.paragraph("general body")),
            sl.Tab("privacy", "Privacy", sl.paragraph("privacy body")),
        ],
        key="settings",
        heading="Settings",
    )
    mount = Mount(tabs, timeout=None)

    view = commit_render(mount)
    assert "general body" in _texts(view)
    assert "privacy body" not in _texts(view)

    await mount.dispatch("settings.privacy", fake_interaction())
    view = commit_render(mount)
    assert tabs.selected == "privacy"
    assert "privacy body" in _texts(view)
    assert "general body" not in _texts(view)

    many = sl.Tabs(
        [sl.Tab(str(index), f"Tab {index}", sl.paragraph(str(index))) for index in range(6)],
        key="many-tabs",
    )
    many_mount = Mount(many, timeout=None)
    many_view = commit_render(many_mount)
    select = next(item for item in many_view.walk_children() if isinstance(item, discord.ui.Select))
    assert select.custom_id is not None and select.custom_id.endswith(":many-tabs")
    assert len(select.options) == 6

    await many_mount.dispatch("many-tabs", fake_interaction(), ["4"])
    assert many.selected == "4"


async def test_tabs_can_embed_the_selected_component_only() -> None:
    tabs = sl.Tabs(
        [sl.Tab("one", "One", Screen("one")), sl.Tab("two", "Two", Screen("two"))],
        key="screens",
    )
    mount = Mount(tabs, timeout=None)

    view = commit_render(mount)
    assert "screen: one" in _texts(view)
    assert "screen: two" not in _texts(view)
    assert set(mount._handlers) == {"screens.one", "screens.two"}


async def test_menu_drills_down_and_owns_back_home_and_close() -> None:
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
    )
    mount = Mount(menu, timeout=None)

    view = commit_render(mount)
    assert _texts(view) == ["## Settings"]
    assert _labels(view)[-2:] == ["Back", "Close"]

    await mount.dispatch("settings.administration", fake_interaction())
    view = commit_render(mount)
    assert menu.path == ("administration",)
    assert "screen: administration" in _texts(view)
    assert "Audit" in _labels(view)
    assert "Back" in _labels(view)

    await mount.dispatch("settings.audit", fake_interaction())
    view = commit_render(mount)
    assert menu.path == ("administration", "audit")
    assert "audit body" in _texts(view)
    assert "Home" in _labels(view)

    await mount.dispatch("settings.home", fake_interaction())
    assert menu.path == ()
    await mount.dispatch("settings.close", fake_interaction())
    assert mount._finished


def test_menu_entry_supports_shorthand_keys_and_rejects_duplicate_destinations() -> None:
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


def test_ranked_list_preserves_global_numbering_and_projects_entries() -> None:
    ranked = sl.RankedList(
        [Score("Ada", 30), Score("Grace", 20), Score("Edsger", 10)],
        key="leaderboard",
        label="name",
        value=lambda entry: entry.points,
        heading="Leaderboard",
        header=lambda total: sl.paragraph(f"Showing {total} entries"),
        footer=lambda total: sl.note(f"Total: {total}"),
        page_size=2,
    )
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, List))
    assert listing.ordered
    assert listing.page_size == 2
    assert [item.content for item in listing.items] == [
        "**Ada** — 30",
        "**Grace** — 20",
        "**Edsger** — 10",
    ]

    mount = Mount(ranked, timeout=None)
    view = commit_render(mount)
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(view)
    assert "Showing 3 entries" in _texts(view)


async def test_ranked_list_keeps_global_ranks_on_later_pages() -> None:
    ranked = sl.RankedList([("Ada", 30), ("Grace", 20), ("Edsger", 10)], key="leaderboard", page_size=2)
    mount = Mount(ranked, timeout=None)
    commit_render(mount)

    await mount.dispatch("__page_next.leaderboard", fake_interaction())
    view = commit_render(mount)
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
    )
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, List))
    assert [item.key for item in listing.items] == ["ada", "grace"]


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"limit": 0}, {"page_size": 0}])
def test_ranked_list_rejects_non_positive_limits(kwargs) -> None:
    with pytest.raises(ValueError):
        sl.RankedList([], key="ranked", **kwargs)
