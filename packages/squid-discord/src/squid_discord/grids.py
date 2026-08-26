"""Exact Discord grid construction."""

from collections.abc import Awaitable, Callable

from squid_ui.grids import GridCell, validate_grid
from squid_ui.interactions import PressEvent, SelectionEvent
from squid_ui.palette import Tone
from squid_ui.primitives import ActionStyle, Button, Row


def _style(tone: Tone) -> ActionStyle:
    return {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(tone, ActionStyle.SECONDARY)


def button_grid(
    *cells: GridCell,
    key: str,
    columns: int,
    on_pick: Callable[[SelectionEvent], Awaitable[None]],
) -> tuple[Row, ...]:
    """Build exact button rows; target planning rejects any Discord-illegal shape."""
    if not key:
        message = "button grid key must not be empty"
        raise ValueError(message)
    declared = tuple(cells)
    validate_grid(declared, columns)
    rows: list[Row] = []
    for start in range(0, len(declared), columns):
        buttons: list[Button] = []
        for cell in declared[start : start + columns]:

            async def pick(event: PressEvent, cell_key: str = cell.key) -> None:
                await on_pick(SelectionEvent(event.actor, event.responder, event.locale, event.context, (cell_key,)))

            buttons.append(
                Button(
                    cell.label,
                    pick,
                    f"{key}.{cell.key}",
                    style=_style(cell.tone),
                    disabled=not cell.available,
                )
            )
        rows.append(Row(tuple(buttons)))
    return tuple(rows)


__all__ = ["button_grid"]
