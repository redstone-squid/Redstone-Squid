"""Resource-backed master-detail browsing."""

from dataclasses import dataclass
from typing import override

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import stage
from squid_ui_widgets._window import source_navigation_state


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    label: str


async def _loaded(
    items: tuple[Entry, ...], *, extent: int = 2, fixed_groups: bool = False
) -> tuple[sl.sources.BatchSource[Entry], sl.sources.LoadedBatch[Entry]]:
    source = sl.sources.list_source(items, fixed_groups=fixed_groups)
    loaded = await sl.sources.BatchLoader(source, extent, lambda item: item.key).load()
    assert loaded is not None
    return source, loaded


async def test_opening_builds_one_detail_component_per_entry_and_back_closes_it() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    built: list[str] = []

    class Detail(sl.Component[ComponentsV2Target]):
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
        fetch_size=2,
    )
    browser.session.replace(loaded)

    async with stage(browser, owner=object()) as ui:
        await ui.select(ui.key_for("browser.open"), ["a"])
        assert browser.opened == Entry("a", "A")
        assert built == ["a"], "one detail per open, not one per render"
        await ui.root.refresh()
        await ui.press(ui.key_for("browser.item-next"))
        assert browser.opened == Entry("b", "B")
        assert built == ["a", "b"]
        await ui.press(ui.key_for("browser.back"))
        assert browser.opened is None
        assert not ui.root.presentation.pending_reveals


async def test_on_back_replaces_returning_to_the_list() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    exits: list[str] = []

    async def leave(_event: sl.ActionEvent) -> None:
        exits.append("left")

    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        fetch_size=2,
        on_back=leave,
    )
    browser.session.replace(loaded)
    await engine.choose(browser, "browser.open", "a")
    await browser._detail.resolve()

    await engine.press(browser, "browser.back")

    assert exits == ["left"]
    assert browser.opened == Entry("a", "A")


async def test_navigating_keeps_the_previous_window_visible_while_the_next_is_pending() -> None:
    """Retain the loaded page until its replacement is ready."""
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")))
    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        fetch_size=2,
    )
    browser.session.replace(loaded)

    browser.session.move("next", revision=browser.session.revision)

    assert browser.session.busy
    assert browser.session.snapshot is not None and browser.session.snapshot.loaded == loaded
    rendered = "\n".join(engine.texts(engine.render_tree(browser)) + engine.labels(engine.render_tree(browser)))
    assert "A" in rendered
    assert "Loading" in rendered


async def test_the_overview_hook_receives_the_loaded_window() -> None:
    source, loaded = await _loaded((Entry("a", "A"),))
    seen: list[sl.sources.LoadedBatch[Entry]] = []
    browser = sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        overview=lambda current: (seen.append(current), sl.note("Warning"))[1],
    )
    browser.session.replace(loaded)

    nodes = engine.render_tree(browser)

    assert nodes
    assert seen == [loaded]
    assert "Warning" in engine.texts(nodes)


def _paged_browser(
    source: sl.sources.BatchSource[Entry], chronology: sl.sources.Chronology | None = None
) -> sp.Browser[Entry]:
    return sp.Browser(
        source,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        detail=lambda item: item.label,
        fetch_size=2,
        chronology=chronology,
    )


async def test_paging_says_previous_and_next_unless_the_list_is_sorted_by_time() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")))
    plain = _paged_browser(source)
    plain.session.replace(loaded)
    newest_first = _paged_browser(source, sl.sources.Chronology.NEWEST_FIRST)
    newest_first.session.replace(loaded)

    assert "Next" in engine.labels(engine.render_tree(plain))
    assert not {"Older", "Newer"} & set(engine.labels(engine.render_tree(plain)))
    # The next page of a newest-first list holds older items.
    assert "Older" in engine.labels(engine.render_tree(newest_first))


async def test_stepping_between_items_follows_the_list_order_in_time() -> None:
    source, loaded = await _loaded((Entry("a", "A"), Entry("b", "B")))
    browser = _paged_browser(source, sl.sources.Chronology.OLDEST_FIRST)
    browser.session.replace(loaded)

    await engine.choose(browser, "browser.open", "a")
    await browser._detail.resolve()
    tree = engine.render_tree(browser)

    assert engine.labels(engine.find(tree, sl.semantic.ActionControl, key="browser.item-previous")) == ["Older"]
    assert engine.labels(engine.find(tree, sl.semantic.ActionControl, key="browser.item-next")) == ["Newer"]


async def test_a_single_item_has_no_step_buttons() -> None:
    source, loaded = await _loaded((Entry("a", "A"),))
    browser = _paged_browser(source)
    browser.session.replace(loaded)

    await engine.choose(browser, "browser.open", "a")
    await browser._detail.resolve()

    tree = engine.render_tree(browser)
    assert not {"browser.item-previous", "browser.item-next"} & set(engine.keys(tree))
    assert "browser.back" in engine.keys(tree)


async def test_page_numbers_appear_only_when_there_is_more_than_one_page() -> None:
    one_page_source, one_page = await _loaded((Entry("a", "A"), Entry("b", "B")))
    single = _paged_browser(one_page_source)
    single.session.replace(one_page)
    two_page_source, two_pages = await _loaded((Entry("a", "A"), Entry("b", "B"), Entry("c", "C")), fixed_groups=True)
    paged = _paged_browser(two_page_source)
    paged.session.replace(two_pages)

    assert not [text for text in engine.texts(engine.render_tree(single)) if "of 1" in text]
    assert [text for text in engine.texts(engine.render_tree(paged)) if "1 of 2" in text]


async def test_source_count_is_not_a_layout_page_count() -> None:
    source = sl.sources.list_source(tuple(str(i) for i in range(23)))
    loaded = await sl.sources.BatchLoader(source, 10, str).load()
    assert loaded is not None
    facts = source_navigation_state(loaded, source.capabilities, key="results", fetch_size=10, chrome=DEFAULT_CHROME)
    assert facts is not None
    assert facts.extent is None and facts.index is None
    assert facts.total == 23 and facts.visible_range == (1, 10)

    fixed = sl.sources.list_source(tuple(str(i) for i in range(23)), fixed_groups=True)
    loaded = await sl.sources.BatchLoader(fixed, 10, str).load()
    assert loaded is not None
    facts = source_navigation_state(loaded, fixed.capabilities, key="results", fetch_size=10, chrome=DEFAULT_CHROME)
    assert facts is not None
    assert facts.extent == 3 and facts.index == 0
    assert facts.unit.value == "groups"
