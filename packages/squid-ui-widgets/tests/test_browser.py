"""Resource-backed master-detail browsing."""

from dataclasses import dataclass

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine


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


async def test_opening_builds_one_detail_component_per_entry_and_back_closes_it() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    built: list[str] = []

    class Detail(sl.Component[sl.ComponentsV2Target]):
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

    # `browser.open` is one picker over the entries. Discord lowers a two-option single-select
    # into buttons keyed `browser.open.a`, which is why the mounted version of this test pressed
    # that id -- but the machine emits a choice, and choosing is what it actually understands.
    await engine.choose(browser, "browser.open", "a")

    assert browser.opened == Entry("a", "A")
    assert built == ["a"], "one detail per open, not one per render"

    await engine.press(browser, "browser.item-next")

    assert browser.opened == Entry("b", "B")
    assert built == ["a", "b"]

    await engine.press(browser, "browser.back")

    assert browser.opened is None


async def test_navigating_keeps_the_previous_window_visible_while_the_next_is_pending() -> None:
    """A reader mid-page should not watch the list empty itself while the source thinks."""
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")))
    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        page_size=2,
    )
    browser.window.replace(loaded)

    browser._request = type(browser._request)("next")

    assert isinstance(browser.window.status, sl.resources.Pending)
    assert browser.window.status.previous == sl.resources.Ready(loaded)
    rendered = "\n".join(engine.texts(engine.render_tree(browser)) + engine.labels(engine.render_tree(browser)))
    assert "A" in rendered
    assert "Loading" in rendered


async def test_the_overview_hook_receives_the_loaded_window() -> None:
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

    nodes = engine.render_tree(browser)

    assert nodes
    assert seen == [loaded]
    assert "Warning" in engine.texts(nodes)
