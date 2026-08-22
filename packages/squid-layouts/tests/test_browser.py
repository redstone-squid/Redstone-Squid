"""Resource-backed master-detail browsing."""

from dataclasses import dataclass

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount
from squid_layouts.discord.testing import commit_render, fake_interaction


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    label: str


async def _loaded(
    items: tuple[Entry, ...], *, extent: int = 2
) -> tuple[sl.WindowSource[Entry], sl.LoadedWindow[Entry]]:
    source = sl.list_source(items)
    loaded = await sl.WindowLoader(source, extent, lambda item: item.key).load()
    assert loaded is not None
    return source, loaded


async def test_list_source_returns_exact_offset_windows() -> None:
    source = sl.list_source(("a", "b", "c"))

    window = await source.fetch(sl.Position(offset=1), 2)

    assert window.items == ("b", "c")
    assert window.position == sl.Position(offset=1)
    assert window.has_previous
    assert not window.has_next
    assert window.total == 3
    assert source.capabilities == sl.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.CountPrecision.EXACT,
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

    browser = sl.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=detail,
        page_size=2,
    )
    browser.window.replace(loaded)
    mount = Mount(browser, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("browser.open", fake_interaction(), ["a"])
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
    browser = sl.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        page_size=2,
    )
    browser.window.replace(loaded)
    mount = Mount(browser, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("browser.next", fake_interaction())

    assert isinstance(browser.window.state, sl.Pending)
    assert browser.window.state.previous == sl.Ready(loaded)
    assert "A" in str(browser.render())
    assert "Loading" in str(browser.render())


async def test_browser_overview_receives_the_loaded_window() -> None:
    source, loaded = await _loaded((Entry("a", "A"),))
    seen: list[sl.LoadedWindow[Entry]] = []
    browser = sl.Browser(
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
