"""Pins event types through action bindings under the project type check; nothing here runs."""

from typing import assert_type

from squid_ui.interactions import ActionBinding, PressEvent, SelectionEvent


async def press(_event: PressEvent) -> None: ...


async def select(_event: SelectionEvent) -> None: ...


assert_type(ActionBinding("press", press), ActionBinding[PressEvent])
assert_type(ActionBinding("select", select), ActionBinding[SelectionEvent])
