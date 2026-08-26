"""The shared two-shell rule and the first three pattern specifications."""

from dataclasses import dataclass

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
import squid_ui_widgets as sp
from squid_ui import scene
from squid_ui.planning.navigation import SEEK_OPTION_LIMIT, NavigationContext, _seek_pages, page_select_nav
from squid_ui.primitives import Button, Lines, Row
from squid_ui.semantic import Stack
from squid_ui.sources import Position, Window
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import commit_render, delivered_to, fake_interaction, fake_message


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
    pattern = sp.Tabs(
        [
            sp.Tab("general", "General", sl.paragraph("general body")),
            sp.Tab("privacy", "Privacy", sl.paragraph("privacy body")),
        ],
        key="settings",
        heading="Settings",
    )
    tabs = pattern.build_component()
    message_root = MessageRoot(tabs, access=Everyone(), timeout=None)

    view = commit_render(message_root)
    assert "general body" in _texts(view)
    assert "privacy body" not in _texts(view)

    await message_root.dispatch("settings.privacy", fake_interaction())
    view = commit_render(message_root)
    assert tabs.machine_state == sp.TabsState("privacy")
    assert "privacy body" in _texts(view)
    assert "general body" not in _texts(view)

    many_pattern = sp.Tabs(
        [sp.Tab(str(index), f"Tab {index}", sl.paragraph(str(index))) for index in range(6)],
        key="many-tabs",
    )
    many = many_pattern.build_component()
    many_root = MessageRoot(many, access=Everyone(), timeout=None)
    many_view = commit_render(many_root)
    select = next(item for item in many_view.walk_children() if isinstance(item, discord.ui.Select))
    assert select.custom_id is not None and select.custom_id.endswith(":many-tabs")
    assert len(select.options) == 6

    await many_root.dispatch("many-tabs", fake_interaction(), ["4"])
    assert many.machine_state == sp.TabsState("4")


async def test_tabs_component_shell_embeds_only_selected_content() -> None:
    tabs = sp.Tabs(
        [sp.Tab("one", "One", Screen("one")), sp.Tab("two", "Two", Screen("two"))],
        key="screens",
    ).build_component()
    message_root = MessageRoot(tabs, access=Everyone(), timeout=None)

    view = commit_render(message_root)
    assert "screen: one" in _texts(view)
    assert "screen: two" not in _texts(view)
    assert set(message_root._handlers) == {"screens.one", "screens.two"}


def test_tabs_router_shell_encodes_next_state_and_input_state() -> None:
    small = sp.Tabs(
        [sp.Tab("one", "One", "one"), sp.Tab("two", "Two", "two")],
        key="tabs",
    )
    routes: list[sp.TransitionRoute[sp.TabsState]] = []

    def route(request: sp.TransitionRoute[sp.TabsState]) -> str:
        routes.append(request)
        return f"tabs:{request.state.selected}"

    rendered = sp.RouteDriver(route).render(small, small.initial_state)
    document = sl.planning.plan(rendered, target=squid_ui_discord.DISCORD_V2_DPY27).scene
    buttons = [item for row in document.components_v2.children if hasattr(row, "items") for item in row.items]
    assert all(isinstance(item, scene.RoutedButton) for item in buttons)
    assert routes[-1] == sp.TransitionRoute("select:two", sp.TabsState("two"), "next")

    many = sp.Tabs([sp.Tab(str(index), str(index), str(index)) for index in range(6)], key="many")
    routed = sp.RouteDriver(route).render(many, many.initial_state)
    routed_scene = sl.planning.plan(routed, target=squid_ui_discord.DISCORD_V2_DPY27).scene
    assert isinstance(routed_scene.components_v2.children[0], scene.RoutedSelect)
    assert routes[-1] == sp.TransitionRoute("select", sp.TabsState("0"), "input")


async def test_menu_component_shell_drills_down_and_owns_chrome() -> None:
    menu = sp.Menu(
        "Settings",
        [
            sp.MenuEntry("appearance", "Appearance", sl.paragraph("appearance body")),
            sp.MenuEntry(
                "Administration",
                Screen("administration"),
                entries=[sp.MenuEntry("Audit", sl.paragraph("audit body"), key="audit")],
            ),
        ],
        key="settings",
    ).build_component()
    message_root = MessageRoot(menu, access=Everyone(), timeout=None)

    view = commit_render(message_root)
    assert _texts(view) == ["## Settings"]
    assert _labels(view)[-3:] == ["Back", "Home", "Close"]

    await message_root.dispatch("settings.administration", fake_interaction())
    view = commit_render(message_root)
    assert menu.machine_state == sp.MenuState(("administration",))
    assert "screen: administration" in _texts(view)
    assert "Audit" in _labels(view)

    await message_root.dispatch("settings.audit", fake_interaction())
    assert menu.machine_state == sp.MenuState(("administration", "audit"))
    await message_root.dispatch("settings.home", fake_interaction())
    assert menu.machine_state == sp.MenuState()
    await message_root.dispatch("settings.close", fake_interaction())
    assert message_root._finished


def test_menu_entry_supports_shorthand_keys_and_rejects_duplicates() -> None:
    shorthand = sp.MenuEntry("Appearance", sl.paragraph("body"))
    assert shorthand.key == "appearance"

    with pytest.raises(ValueError, match="keys must be unique"):
        sp.Menu(
            "Settings",
            [sp.MenuEntry("Same", sl.paragraph("one")), sp.MenuEntry("Same", sl.paragraph("two"))],
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
        capabilities: sl.sources.SourceCapabilities,
    ) -> None:
        self.entries = entries
        self.capabilities = capabilities
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        self.requests.append(position)
        keys = tuple(label for label, _score in self.entries)
        if position.anchor in keys:
            anchor = keys.index(position.anchor)
            if position.direction is sl.sources.Direction.FORWARD:
                offset = anchor + 1
            elif position.direction is sl.sources.Direction.BACKWARD:
                offset = max(0, anchor - extent)
            else:
                offset = anchor
        else:
            offset = position.offset
        visible = self.entries[offset : offset + extent]
        total = len(self.entries) if self.capabilities.count is not sl.sources.CountPrecision.NONE else None
        resolved_anchor = visible[0][0] if visible else None
        return Window(
            Position(resolved_anchor, offset),
            visible,
            has_previous=offset > 0 and self.capabilities.backward,
            has_next=offset + extent < len(self.entries),
            total=total,
        )


class FlakyScoreSource(ScoreSource):
    def __init__(self, entries: tuple[tuple[str, int], ...], *, capabilities: sl.sources.SourceCapabilities) -> None:
        super().__init__(entries, capabilities=capabilities)
        self.fail_next = False

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("source unavailable")
        return await super().fetch(position, extent)


def test_ranked_list_projects_entries_and_renders_an_explicit_window() -> None:
    ranked = sp.RankedList(
        [Score("Ada", 30), Score("Grace", 20), Score("Edsger", 10)],
        key="leaderboard",
        label="name",
        value=lambda entry: entry.points,
        heading="Leaderboard",
        header=lambda total: sl.paragraph(f"Showing {total} entries"),
        footer=lambda total: sl.note(f"Total: {total}"),
        page_size=2,
    ).build_component()
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, Lines))
    assert listing.lines == ("1. **Ada** — 30", "2. **Grace** — 20")

    view = commit_render(MessageRoot(ranked, access=Everyone(), timeout=None))
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(view)

    assert "Showing 3 entries" in _texts(view)
    assert "-# Page 1 of 2" in _texts(view)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        (
            sl.sources.SourceCapabilities(offsets=True, jumpable=True, count=sl.sources.CountPrecision.EXACT),
            "-# Page 1 of 2",
        ),
        (sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT), "-# 1\N{EN DASH}2 of 3"),
        (
            sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.APPROXIMATE),
            "-# 1\N{EN DASH}2 of ~3",
        ),
        (sl.sources.SourceCapabilities(offsets=True), "-# 1\N{EN DASH}2"),
        (sl.sources.SourceCapabilities(), None),
    ],
)
async def test_source_ranked_list_gates_numeric_chrome_by_capability(
    capabilities: sl.sources.SourceCapabilities, expected: str | None
) -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=capabilities,
    )
    ranked = sp.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2)
    message_root = MessageRoot(ranked, access=Everyone(), timeout=None)

    await message_root.send(delivered_to(fake_message()))

    assert message_root._view is not None
    numeric = [text for text in _texts(message_root._view) if text.startswith("-#")]
    assert numeric == ([] if expected is None else [expected])


async def test_source_ranked_list_fetches_in_handlers_and_uses_source_navigation() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.sources.SourceCapabilities(
            backward=False,
            offsets=True,
            jumpable=True,
            count=sl.sources.CountPrecision.EXACT,
        ),
    )
    ranked = sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2)
    message_root = MessageRoot(ranked, access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))

    assert message_root._view is not None
    assert _labels(message_root._view) == ["Newer"]
    interaction = fake_interaction()
    await message_root.dispatch("stream.next", interaction)

    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(pending)
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.sources.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(edited)
    assert "-# Page 2 of 3" in _texts(edited)


async def test_source_ranked_list_retains_stale_rows_and_retries_the_failed_request() -> None:
    source = FlakyScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5)),
        capabilities=sl.sources.SourceCapabilities(
            backward=True,
            offsets=True,
            jumpable=True,
            count=sl.sources.CountPrecision.EXACT,
        ),
    )
    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
    )
    await message_root.send(delivered_to(fake_message()))

    source.fail_next = True
    failed_interaction = fake_interaction()
    await message_root.dispatch("stream.next", failed_interaction)

    failed = failed_interaction.followup.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(failed)
    assert "-# Could not load entries." in _texts(failed)
    assert "Retry" in _labels(failed)

    retry_interaction = fake_interaction()
    await message_root.dispatch("stream.retry", retry_interaction)

    pending = retry_interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)
    settled = retry_interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.sources.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(settled)


async def test_a_jumpable_source_seeks_by_page() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, jumpable=True, count=sl.sources.CountPrecision.EXACT),
    )
    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=page_select_nav,
    )
    await message_root.send(delivered_to(fake_message()))

    interaction = fake_interaction()
    await message_root.dispatch("stream.seek", interaction, ["2"])

    # Page 2 of a page_size=2 source is item offset 4, not item offset 2.
    assert source.requests[-1] == Position(offset=4)
    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert "-# Page 3 of 3" in _texts(edited)


async def test_a_sequential_source_offers_no_jump_control() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT),
    )
    seen: list[NavigationContext] = []

    def nav(context):
        seen.append(context)
        return page_select_nav(context)

    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await message_root.send(delivered_to(fake_message()))

    assert seen[-1].on_seek is None
    assert message_root._view is not None
    assert not [item for item in message_root._view.walk_children() if isinstance(item, discord.ui.Select)]


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


async def test_source_ranked_list_uses_the_message_root_navigation_factory() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT),
    )
    seen = []

    def nav(context):
        seen.append(context.state)
        return (Row((Button("More", context.on_next, "more"),)),)

    message_root = MessageRoot(
        sp.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await message_root.send(delivered_to(fake_message()))

    assert seen[-1].key == "leaderboard"
    assert seen[-1].visible_range == (1, 2)
    assert seen[-1].total == 3
    assert message_root._view is not None and _labels(message_root._view) == ["More"]


async def test_ranked_list_keeps_global_ranks_on_later_pages() -> None:
    ranked = sp.RankedList(
        [("Ada", 30), ("Grace", 20), ("Edsger", 10)], key="leaderboard", page_size=2
    ).build_component()
    message_root = MessageRoot(ranked, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("leaderboard.next", fake_interaction())
    view = commit_render(message_root)
    assert ranked.machine_state == sp.RankedListState(Position(offset=1))
    assert "3. **Edsger** — 10" in _texts(view)


def test_ranked_list_top_n_and_explicit_entries() -> None:
    ranked = sp.RankedList(
        [
            sp.RankedEntry("Ada", 30, key="ada"),
            sp.RankedEntry("Grace", 20, key="grace"),
            sp.RankedEntry("Edsger", 10, key="edsger"),
        ],
        key="top",
        top_n=2,
    ).build_component()
    rendered = ranked.render()
    assert isinstance(rendered, Stack)
    listing = next(node for node in rendered.children if isinstance(node, Lines))
    assert listing.lines == ("1. **Ada** — 30", "2. **Grace** — 20")


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"limit": 0}, {"page_size": 0}])
def test_ranked_list_rejects_non_positive_limits(kwargs) -> None:
    with pytest.raises(ValueError):
        sp.RankedList([], key="ranked", **kwargs)
