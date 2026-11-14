"""Shared grid offers preserve positions and expose only visible cell actions."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.grids import GridCell
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "grid", "1")
SLACK = AdapterProfile(SlackAdapter, "grid", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    sl.html.target(),
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]
IDENTITY = OccurrenceId(("root", "board"))


async def untouched(event: sl.SelectionEvent) -> None:
    pytest.fail("planning called a cell handler")


def grid(count: int, *, columns: int = 2) -> sl.semantic.Grid:
    return sl.grid(
        *(GridCell(str(index), f"Cell {index}") for index in range(count)),
        key="board",
        columns=columns,
        on_pick=untouched,
    )


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_row_pages_keep_columns_and_publish_only_visible_cells(target: Target[Any, Any, Any, Any]) -> None:
    options = CompositionOptions(
        automatic=True, selections={IDENTITY: "grid.buttons"}, partitions={IDENTITY: (2, 4, 6)}
    )
    session = PresentationState()
    result = plan(grid(6), target=target, composition=options, session=session, strict=True)
    assert result.allocation is not None
    for index in range(3):
        page = result.for_page({IDENTITY: index})
        assert {key for key in page.bindings if key.startswith("board.")} == {
            f"board.{index * 2}",
            f"board.{index * 2 + 1}",
        }
        if isinstance(page.scene.body, scene.HtmlBody):
            table = page.scene.body.children[0]
            assert isinstance(table, scene.HtmlElement) and table.tag == scene.HtmlTag.TABLE
            assert scene.HtmlAttribute(scene.HtmlAttributeName.ROLE, "grid") in table.attributes
    session.apply_updates(result.session_updates)
    repeated = plan(grid(6), target=target, composition=options, session=session, strict=True)
    assert repeated.allocation is result.allocation


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_coordinate_pages_preserve_unavailable_cells_as_context(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.grid(
        GridCell("closed0", "Closed zero", available=False),
        GridCell("one", "First"),
        GridCell("closed1", "Closed one", available=False),
        GridCell("two", "Second"),
        key="board",
        columns=6,
        on_pick=untouched,
    )
    result = plan(
        node,
        target=target,
        strict=True,
        composition=CompositionOptions(
            automatic=True, selections={IDENTITY: "grid.coordinates"}, partitions={IDENTITY: (1, 2)}
        ),
    )
    assert result.allocation is not None
    first, second = (result.for_page({IDENTITY: index}) for index in range(2))
    first_wire, second_wire = scene.Codec.dumps(first.scene), scene.Codec.dumps(second.scene)
    assert "A1 [x] Closed zero" in first_wire and "C1 [x] Closed one" in first_wire
    assert "B1" in first_wire and "First" in first_wire
    assert "D1" in second_wire and "Second" in second_wire
    assert "Closed zero" not in second_wire and "Closed one" not in second_wire
    assert not first.report.events and not second.report.events


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_grid_without_available_cells_has_no_selector_or_live_binding(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.grid(GridCell("closed", "Closed", available=False), key="board", columns=6, on_pick=untouched)
    result = plan(
        node, target=target, composition=CompositionOptions(automatic=True, selections={IDENTITY: "grid.readonly"})
    )
    assert "A1 [x] Closed" in scene.Codec.dumps(result.scene)
    assert not result.bindings


def test_equivalent_grid_presentation_preserves_cell_positions_and_callbacks() -> None:
    source = grid(4)
    for changed in (replace(source, columns=4), replace(source, cells=tuple(reversed(source.cells)))):
        choice = sl.alternative(changed, key="changed", fidelity=sl.equivalent())
        with pytest.raises(LayoutInvariantError, match="boundary"):
            check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)

    async def changed_handler(event: sl.SelectionEvent) -> None:
        pass

    choice = sl.alternative(replace(source, on_pick=changed_handler), key="changed", fidelity=sl.equivalent())
    with pytest.raises(LayoutInvariantError, match="interaction"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)
