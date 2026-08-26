"""Matrix tables and exact or adaptive selectable grids."""

from dataclasses import fields, is_dataclass
from typing import cast

import pytest

import squid_ui_discord
import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_ui.runtime import PresentationSession, apply_updates
from squid_ui.runtime.component import render_component_tree
from squid_ui.runtime.presentation import StrategyUpdate


def _cells(count: int) -> tuple[sp.GridCell, ...]:
    return tuple(sp.GridCell(f"cell-{index}", f"Cell {index}") for index in range(count))


def _walk(value: object):
    yield value
    if isinstance(value, tuple):
        for item in value:
            yield from _walk(item)
    elif is_dataclass(value):
        for item in fields(value):
            yield from _walk(getattr(value, item.name))


async def _pick(_event: sl.SelectionEvent) -> None: ...


def test_explicit_matrix_table_is_dense_and_authoritative() -> None:
    table = sl.table(
        sl.columns(sl.column("Name"), sl.column("Value")),
        sl.table_row("Alpha", "1"),
        sl.table_row("Beta", "2"),
        key="stats",
        display=sl.semantic.TableDisplay.MATRIX,
    )

    result = sl.planning.plan(table, target=squid_ui_discord.DISCORD_V2_DPY27)
    text = next(item.content for item in _walk(result.scene.body) if isinstance(item, scene.Text))

    assert text.splitlines()[1].split() == ["Name", "Value"]
    assert "-+-" not in text
    update = next(
        update for update in result.session_updates if isinstance(update, StrategyUpdate) and update.key == "stats"
    )
    assert update.state.strategy_id == "matrix"


async def test_exact_button_grid_keeps_rows_keys_disabled_state_and_payload() -> None:
    seen: list[sl.SelectionEvent] = []

    async def pick(event: sl.SelectionEvent) -> None:
        seen.append(event)

    cells = (*_cells(6)[:5], sp.GridCell("blocked", "Blocked", available=False))
    result = sl.planning.plan(
        squid_ui_discord.button_grid(*cells, key="board", columns=5, on_pick=pick),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )
    rows = [item for item in _walk(result.scene.body) if isinstance(item, scene.Row)]
    buttons = [item for item in _walk(result.scene.body) if isinstance(item, scene.Button)]

    assert [len(row.items) for row in rows] == [5, 1]
    assert buttons[-1].disabled
    await result.bindings[buttons[0].action].handler(
        sl.PressEvent(sl.interactions.Actor("7"), cast(sl.interactions.ActionResponder, object()))
    )
    assert seen[0].values == ("cell-0",)


def test_exact_button_grid_refuses_illegal_discord_shapes_in_planning() -> None:
    with pytest.raises(LayoutInvariantError, match="maximum is 5"):
        sl.planning.plan(
            squid_ui_discord.button_grid(*_cells(6), key="wide", columns=6, on_pick=_pick),
            target=squid_ui_discord.DISCORD_V2_DPY27,
        )
    with pytest.raises(UnsolvableLayoutError):
        sl.planning.plan(
            squid_ui_discord.button_grid(*_cells(35), key="large", columns=5, on_pick=_pick),
            target=squid_ui_discord.DISCORD_V2_DPY27,
        )


@pytest.mark.parametrize(
    ("count", "columns", "scene_type", "strategy"),
    [
        (5, 5, scene.Button, "buttons"),
        (6, 6, scene.Select, "coordinate"),
        (30, 6, scene.Select, "paged_select"),
    ],
)
def test_semantic_grid_selects_a_legal_strategy(count: int, columns: int, scene_type: type, strategy: str) -> None:
    result = sl.planning.plan(
        sl.grid(*_cells(count), key="board", columns=columns, on_pick=_pick),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )

    assert any(isinstance(item, scene_type) for item in _walk(result.scene.body))
    update = next(
        update for update in result.session_updates if isinstance(update, StrategyUpdate) and update.key == "board"
    )
    assert update.state.strategy_id == strategy
    if strategy == "paged_select":
        select = next(item for item in _walk(result.scene.body) if isinstance(item, scene.Select))
        assert len(select.options) == 25
        assert result.scene.pagers[0].pages == 2


def test_semantic_grid_strategy_remains_sticky_while_available() -> None:
    session = PresentationSession()
    first = sl.planning.plan(
        sl.grid(*_cells(6), key="board", columns=6, on_pick=_pick),
        target=squid_ui_discord.DISCORD_V2_DPY27,
        session=session,
    )
    apply_updates(session, first.session_updates)

    second = sl.planning.plan(
        sl.grid(*_cells(5), key="board", columns=5, on_pick=_pick),
        target=squid_ui_discord.DISCORD_V2_DPY27,
        session=session,
    )

    assert any(isinstance(item, scene.Select) for item in _walk(second.scene.body))
    update = next(
        update for update in second.session_updates if isinstance(update, StrategyUpdate) and update.key == "board"
    )
    assert update.state.strategy_id == "coordinate"


async def test_coordinate_grid_lists_only_available_cells_and_submits_stable_keys() -> None:
    seen: list[sl.SelectionEvent] = []

    async def pick(event: sl.SelectionEvent) -> None:
        seen.append(event)

    result = sl.planning.plan(
        sl.grid(
            sp.GridCell("open", "Open"),
            sp.GridCell("blocked", "Blocked", available=False),
            key="board",
            columns=6,
            on_pick=pick,
        ),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )
    select = next(item for item in _walk(result.scene.body) if isinstance(item, scene.Select))

    assert [(option.label, option.value) for option in select.options] == [("A1 ??Open", "open")]
    await result.bindings[select.action].handler(
        sl.SelectionEvent(
            sl.interactions.Actor("7"),
            cast(sl.interactions.ActionResponder, object()),
            values=("open",),
        )
    )
    assert seen[0].values == ("open",)


def test_grid_authoring_rejects_empty_duplicate_and_nonpositive_shapes() -> None:
    with pytest.raises(ValueError, match="at least one cell"):
        sl.grid(key="board", columns=1, on_pick=_pick)
    with pytest.raises(ValueError, match="unique"):
        sl.grid(*_cells(1), *_cells(1), key="board", columns=1, on_pick=_pick)
    with pytest.raises(ValueError, match="positive"):
        squid_ui_discord.button_grid(*_cells(1), key="board", columns=0, on_pick=_pick)
    with pytest.raises(ValueError, match="key must not be empty"):
        squid_ui_discord.button_grid(*_cells(1), key="", columns=1, on_pick=_pick)


def test_semantic_grid_namespaces_its_key_inside_a_component_boundary() -> None:
    class Child(sl.Component):
        def render(self):
            return sl.grid(*_cells(2), key="board", columns=2, on_pick=_pick)

    class Parent(sl.Component):
        def __init__(self) -> None:
            self.child = Child()

        def render(self):
            return self.boundary(self.child, key="child")

    tree = render_component_tree(Parent())
    grid = cast(sl.semantic.Grid, tree.nodes[0])

    assert grid.key == "child.board"
