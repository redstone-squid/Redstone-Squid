"""RankedList: global ranks over an in-memory sequence, paged or capped."""

from typing import Any

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.primitives import Lines
from squid_ui.sources import Position
from squid_ui_widgets import testing as wt


def _listing(harness: wt.MachineHarness[Any, sl.DiscordTarget]) -> list[str]:
    return [str(line) for line in engine.find(harness.nodes, Lines).lines]


def test_it_projects_entries_and_renders_only_the_first_page() -> None:
    harness = wt.driving(
        sp.RankedList(
            [("Ada", 30), ("Grace", 20), ("Edsger", 10)],
            key="leaderboard",
            heading="Leaderboard",
            header=lambda total: sl.paragraph(f"Showing {total} entries"),
            footer=lambda total: sl.note(f"Total: {total}"),
            page_size=2,
        ).build_component()
    )

    assert _listing(harness) == ["1. **Ada** — 30", "2. **Grace** — 20"]
    assert "Showing 3 entries" in harness.texts()
    assert "Page 1 of 2" in "\n".join(harness.texts())


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

    assert _listing(harness) == ["1. **Ada** — 30", "2. **Grace** — 20"]


async def test_ranks_stay_global_on_a_later_page() -> None:
    """Edsger is third overall, so he is `3.` on page two -- not `1.`."""
    harness = wt.driving(
        sp.RankedList([("Ada", 30), ("Grace", 20), ("Edsger", 10)], key="leaderboard", page_size=2).build_component()
    )

    await harness.press("leaderboard.next")

    assert harness.state == sp.RankedListState(Position(offset=1))
    assert "3. **Edsger** — 10" in _listing(harness)


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

    assert _listing(harness) == ["1. **Ada** — 30", "2. **Grace** — 20"]


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"limit": 0}, {"page_size": 0}])
def test_it_rejects_a_non_positive_limit(kwargs: Any) -> None:
    with pytest.raises(ValueError):
        sp.RankedList([], key="ranked", **kwargs)
