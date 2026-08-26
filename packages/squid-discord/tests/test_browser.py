"""Resource-backed master-detail browsing."""

from dataclasses import dataclass

import squid_ui as sl
import squid_patterns as sp
from squid_discord import Everyone, Mount
from squid_discord.testing import commit_render, fake_interaction


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    label: str


async def _loaded(
    items: tuple[Entry, ...], *, extent: int = 2
) -> tuple[sl.sources.WindowSource[Entry], sl.sources.LoadedWindow[Entry]]:
    source = sl.sources.list_source(items)
    loaded = await sl.sources.WindowLoader(source, extent, lambda item: item.key).load()
    assert loaded is not None
    return source, loaded


async def test_list_source_returns_exact_offset_windows() -> None:
    source = sl.sources.list_source(("a", "b", "c"))

    window = await source.fetch(sl.sources.Position(offset=1), 2)

    assert window.items == ("b", "c")
    assert window.position == sl.sources.Position(offset=1)
    assert window.has_previous
    assert not window.has_next
    assert window.total == 3
    assert source.capabilities == sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )


async def test_browser_opens_and_retains_one_detail_component_per_open() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    built: list[str] = []

    class Detail(sl.Component):
        def __init__(self, entry: Entry) -> None:
            self.entry = entry

        def render(self) -> sl.LayoutNode:
            return sl.paragraph(self.entry.label)

    def detail(entry: Entry) -> Detail:
        built.append(entry.key)
        return Detail(entry)

    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=detail,
        page_size=2,
    )
    browser.window.replace(loaded)
    mount = Mount(browser, access=Everyone(), timeout=None)
    commit_render(mount)

    # Two single-choice entries lower to buttons instead of a select menu.
    await mount.dispatch("browser.open.a", fake_interaction())
    commit_render(mount)
    browser.invalidate()
    commit_render(mount)

    assert browser.opened == Entry("a", "A")
    assert built == ["a"]

    await mount.dispatch("browser.item-next", fake_interaction())
    assert browser.opened == Entry("b", "B")
    assert built == ["a", "b"]

    await mount.dispatch("browser.back", fake_interaction())
    assert browser.opened is None


async def test_browser_navigation_keeps_previous_window_visible_while_pending() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")))
    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        page_size=2,
    )
    browser.window.replace(loaded)
    mount = Mount(browser, access=Everyone(), timeout=None)
    commit_render(mount)

    browser._request = type(browser._request)("next")

    assert isinstance(browser.window.status, sl.resources.Pending)
    assert browser.window.status.previous == sl.resources.Ready(loaded)
    assert "A" in str(browser.render())
    assert "Loading" in str(browser.render())


async def test_browser_overview_receives_the_loaded_window() -> None:
    source, loaded = await _loaded((Entry("a", "A"),))
    seen: list[sl.sources.LoadedWindow[Entry]] = []
    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        overview=lambda current: (seen.append(current), sl.note("Warning"))[1],
    )
    browser.window.replace(loaded)

    rendered = browser.render()

    assert rendered
    assert seen == [loaded]
