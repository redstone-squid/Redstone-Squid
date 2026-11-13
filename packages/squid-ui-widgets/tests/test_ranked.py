"""RankedList: global ranks over an in-memory sequence, paged or capped."""

from typing import TypedDict

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.ownership import Controlled
from squid_ui.primitives import Alt, Lines, Paginate
from squid_ui.text import source_text
from squid_ui_widgets import testing as wt


class _LimitKwargs(TypedDict, total=False):
    top_n: int
    page_size: int


def _listing(nodes: tuple[sl.semantic.AnyLayoutNode, ...]) -> list[str]:
    return [source_text(line.primary if isinstance(line, Alt) else line) for line in engine.find(nodes, Lines).lines]


def _leaderboard() -> sp.RankedList:
    return sp.RankedList(
        [("Ada", 30), ("Grace", 20), ("Edsger", 10)],
        key="leaderboard",
        title="Leaderboard",
        header=lambda total: sl.paragraph(f"Showing {total} entries"),
        footer=lambda total: sl.note(f"Total: {total}"),
        page_size=2,
    )


def test_a_mounted_ranking_hands_every_row_to_a_controlled_pager() -> None:
    # The planner slices and draws the pager, so the unplanned tree holds the whole ranking.
    harness = wt.driving(_leaderboard().build_component())

    pager = engine.find(harness.nodes, Lines).overflow
    assert _listing(harness.nodes) == ["1. **Ada** — 30", "2. **Grace** — 20", "3. **Edsger** — 10"]
    assert isinstance(pager, Paginate)
    assert pager.key == "leaderboard"
    assert pager.per == 2
    assert isinstance(pager.page, Controlled) and pager.page.value == 0
    assert "Showing 3 entries" in harness.texts()


async def test_a_mounted_page_turn_stores_the_page_the_pager_asked_for() -> None:
    harness = wt.driving(_leaderboard().build_component())
    pager = engine.find(harness.nodes, Lines).overflow
    assert isinstance(pager, Paginate) and isinstance(pager.page, Controlled)
    page = pager.page

    await page.on_change(engine.page_event(1))

    assert harness.state == sp.RankedListState(1)


def test_a_routed_ranking_plans_its_page_and_numbers_rows_globally() -> None:
    """Edsger is third overall, so he is `3.` on page two -- not `1.`."""
    first = wt.routed(_leaderboard())
    second = wt.routed(_leaderboard(), sp.RankedListState(1))

    assert "1. **Ada** — 30\n2. **Grace** — 20" in first.texts()
    assert any("Page 1 of 2" in text for text in first.texts())
    assert "3. **Edsger** — 10" in second.texts()
    assert any("Page 2 of 2" in text for text in second.texts())
    assert [route.state for route in first.routes] == [sp.RankedListState(0), sp.RankedListState(1)]


def test_a_page_turn_ignores_a_missing_or_malformed_page() -> None:
    ranking = _leaderboard()
    state = sp.RankedListState(1)

    assert ranking.transition(state, "page", values=("0",)) == sp.RankedListState(0)
    assert ranking.transition(state, "page") == state
    assert ranking.transition(state, "page", values=("-1",)) == state


def test_a_label_and_value_may_be_read_off_an_arbitrary_entry() -> None:
    class Score:
        def __init__(self, name: str, points: int) -> None:
            self.name = name
            self.points = points

    harness = wt.driving(
        sp.RankedList(
            [Score("Ada", 30), Score("Grace", 20)],
            key="leaderboard",
            label="name",
            value=lambda entry: entry.points,
        ).build_component()
    )

    assert _listing(harness.nodes) == ["1. **Ada** — 30", "2. **Grace** — 20"]


def test_top_n_caps_the_listing_and_explicit_entries_keep_their_keys() -> None:
    harness = wt.driving(
        sp.RankedList(
            [
                sp.RankedEntry("Ada", 30, key="ada"),
                sp.RankedEntry("Grace", 20, key="grace"),
                sp.RankedEntry("Edsger", 10, key="edsger"),
            ],
            key="top",
            top_n=2,
        ).build_component()
    )

    assert _listing(harness.nodes) == ["1. **Ada** — 30", "2. **Grace** — 20"]


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"page_size": 0}])
def test_it_rejects_a_non_positive_limit(kwargs: _LimitKwargs) -> None:
    with pytest.raises(ValueError):
        sp.RankedList([], key="ranked", **kwargs)
