"""The shared two-shell rule and the first three pattern specifications."""

from dataclasses import dataclass

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount, NavigationContext, page_select_nav
from squid_layouts.discord.testing import commit_render, delivered_to, fake_interaction, fake_message
from squid_layouts.planning.navigation import SEEK_OPTION_LIMIT, _seek_pages
from squid_layouts.primitives import Button, Lines, Row
from squid_layouts.scene import SceneRoutedButton, SceneRoutedSelect
from squid_layouts.semantic import Stack
from squid_layouts.sources import Position, Window


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
    mount = Mount(tabs, access=Everyone(), timeout=None)

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
    many_mount = Mount(many, access=Everyone(), timeout=None)
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
    mount = Mount(tabs, access=Everyone(), timeout=None)

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
    mount = Mount(menu, access=Everyone(), timeout=None)

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


class ScoreSource:
    def __init__(
        self,
        entries: tuple[tuple[str, int], ...],
        *,
        capabilities: sl.SourceCapabilities,
    ) -> None:
        self.entries = entries
        self.capabilities = capabilities
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        self.requests.append(position)
        keys = tuple(label for label, _score in self.entries)
        if position.anchor in keys:
            anchor = keys.index(position.anchor)
            if position.direction is sl.Direction.FORWARD:
                offset = anchor + 1
            elif position.direction is sl.Direction.BACKWARD:
                offset = max(0, anchor - extent)
            else:
                offset = anchor
        else:
            offset = position.offset
        visible = self.entries[offset : offset + extent]
        total = len(self.entries) if self.capabilities.count is not sl.CountPrecision.NONE else None
        resolved_anchor = visible[0][0] if visible else None
        return Window(
            Position(resolved_anchor, offset),
            visible,
            has_previous=offset > 0 and self.capabilities.backward,
            has_next=offset + extent < len(self.entries),
            total=total,
        )


class FlakyScoreSource(ScoreSource):
    def __init__(self, entries: tuple[tuple[str, int], ...], *, capabilities: sl.SourceCapabilities) -> None:
        super().__init__(entries, capabilities=capabilities)
        self.fail_next = False

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("source unavailable")
        return await super().fetch(position, extent)


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

    view = commit_render(Mount(ranked, access=Everyone(), timeout=None))
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(view)

    assert "Showing 3 entries" in _texts(view)
    assert "-# Page 1 of 2" in _texts(view)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        (sl.SourceCapabilities(offsets=True, jumpable=True, count=sl.CountPrecision.EXACT), "-# Page 1 of 2"),
        (sl.SourceCapabilities(offsets=True, count=sl.CountPrecision.EXACT), "-# 1\N{EN DASH}2 of 3"),
        (sl.SourceCapabilities(offsets=True, count=sl.CountPrecision.APPROXIMATE), "-# 1\N{EN DASH}2 of ~3"),
        (sl.SourceCapabilities(offsets=True), "-# 1\N{EN DASH}2"),
        (sl.SourceCapabilities(), None),
    ],
)
async def test_source_ranked_list_gates_numeric_chrome_by_capability(
    capabilities: sl.SourceCapabilities, expected: str | None
) -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=capabilities,
    )
    ranked = sl.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2)
    mount = Mount(ranked, access=Everyone(), timeout=None)

    await mount.send(delivered_to(fake_message()))

    assert mount._view is not None
    numeric = [text for text in _texts(mount._view) if text.startswith("-#")]
    assert numeric == ([] if expected is None else [expected])


async def test_source_ranked_list_fetches_in_handlers_and_uses_source_navigation() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.SourceCapabilities(
            backward=False,
            offsets=True,
            jumpable=True,
            count=sl.CountPrecision.EXACT,
        ),
    )
    ranked = sl.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2)
    mount = Mount(ranked, access=Everyone(), timeout=None)
    await mount.send(delivered_to(fake_message()))

    assert mount._view is not None
    assert _labels(mount._view) == ["Newer"]
    interaction = fake_interaction()
    await mount.dispatch("stream.next", interaction)

    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(pending)
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(edited)
    assert "-# Page 2 of 3" in _texts(edited)


async def test_source_ranked_list_retains_stale_rows_and_retries_the_failed_request() -> None:
    source = FlakyScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5)),
        capabilities=sl.SourceCapabilities(
            backward=True,
            offsets=True,
            jumpable=True,
            count=sl.CountPrecision.EXACT,
        ),
    )
    mount = Mount(
        sl.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
    )
    await mount.send(delivered_to(fake_message()))

    source.fail_next = True
    failed_interaction = fake_interaction()
    await mount.dispatch("stream.next", failed_interaction)

    failed = failed_interaction.followup.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(failed)
    assert "-# Could not load entries." in _texts(failed)
    assert "Retry" in _labels(failed)

    retry_interaction = fake_interaction()
    await mount.dispatch("stream.retry", retry_interaction)

    pending = retry_interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)
    settled = retry_interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(settled)


async def test_a_jumpable_source_seeks_by_page() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.SourceCapabilities(offsets=True, jumpable=True, count=sl.CountPrecision.EXACT),
    )
    mount = Mount(
        sl.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=page_select_nav,
    )
    await mount.send(delivered_to(fake_message()))

    interaction = fake_interaction()
    await mount.dispatch("stream.seek", interaction, ["2"])

    # Page 2 of a page_size=2 source is item offset 4, not item offset 2.
    assert source.requests[-1] == Position(offset=4)
    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert "-# Page 3 of 3" in _texts(edited)


async def test_a_sequential_source_offers_no_jump_control() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.SourceCapabilities(offsets=True, count=sl.CountPrecision.EXACT),
    )
    seen: list[NavigationContext] = []

    def nav(context):
        seen.append(context)
        return page_select_nav(context)

    mount = Mount(
        sl.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await mount.send(delivered_to(fake_message()))

    assert seen[-1].on_seek is None
    assert mount._view is not None
    assert not [item for item in mount._view.walk_children() if isinstance(item, discord.ui.Select)]


@pytest.mark.parametrize(
    ("page", "extent"),
    [(0, 2), (0, 25), (12, 26), (0, 200), (137, 200), (199, 200), (500, 5000)],
)
def test_a_jump_select_always_fits_and_always_offers_the_visible_page(page: int, extent: int) -> None:
    pages = _seek_pages(page, extent)

    assert len(pages) <= SEEK_OPTION_LIMIT
    assert pages == sorted(set(pages))
    assert page in pages
    assert pages[0] == 0
    assert pages[-1] == extent - 1


async def test_source_ranked_list_uses_the_mount_navigation_factory() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.SourceCapabilities(offsets=True, count=sl.CountPrecision.EXACT),
    )
    seen = []

    def nav(context):
        seen.append(context.state)
        return (Row((Button("More", context.on_next, "more"),)),)

    mount = Mount(
        sl.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await mount.send(delivered_to(fake_message()))

    assert seen[-1].key == "leaderboard"
    assert seen[-1].visible_range == (1, 2)
    assert seen[-1].total == 3
    assert mount._view is not None and _labels(mount._view) == ["More"]


async def test_ranked_list_keeps_global_ranks_on_later_pages() -> None:
    ranked = sl.RankedList([("Ada", 30), ("Grace", 20), ("Edsger", 10)], key="leaderboard", page_size=2).component()
    mount = Mount(ranked, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("leaderboard.next", fake_interaction())
    view = commit_render(mount)
    assert ranked.pattern_state == sl.RankedListState(Position(offset=1))
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
