"""Resource-backed master-detail browsing."""

from dataclasses import dataclass
from typing import override

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

        @override
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

    # Drive the semantic picker; adapters may lower its options into separate buttons.
    await engine.choose(browser, "browser.open", "a")

    assert browser.opened == Entry("a", "A")
    assert built == ["a"], "one detail per open, not one per render"

    await engine.press(browser, "browser.item-next")

    assert browser.opened == Entry("b", "B")
    assert built == ["a", "b"]

    await engine.press(browser, "browser.back")

    assert browser.opened is None


async def test_navigating_keeps_the_previous_window_visible_while_the_next_is_pending() -> None:
    """Retain the loaded page until its replacement is ready."""
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


def _paged_browser(
    source: sl.sources.WindowSource[Entry], chronology: sl.sources.Chronology | None = None
) -> sp.Browser[Entry]:
    return sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        page_size=2,
        chronology=chronology,
    )


async def test_paging_says_previous_and_next_unless_the_list_is_sorted_by_time() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")))
    plain = _paged_browser(source)
    plain.window.replace(loaded)
    newest_first = _paged_browser(source, sl.sources.Chronology.NEWEST_FIRST)
    newest_first.window.replace(loaded)

    assert "Next" in engine.labels(engine.render_tree(plain))
    assert not {"Older", "Newer"} & set(engine.labels(engine.render_tree(plain)))
    # The next page of a newest-first list holds older items.
    assert "Older" in engine.labels(engine.render_tree(newest_first))


async def test_stepping_between_items_follows_the_list_order_in_time() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    browser = _paged_browser(source, sl.sources.Chronology.OLDEST_FIRST)
    browser.window.replace(loaded)

    await engine.choose(browser, "browser.open", "a")
    tree = engine.render_tree(browser)

    assert engine.labels(engine.find(tree, sl.semantic.ActionControl, key="browser.item-previous")) == ["Older"]
    assert engine.labels(engine.find(tree, sl.semantic.ActionControl, key="browser.item-next")) == ["Newer"]
