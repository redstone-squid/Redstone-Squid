"""Pins event types through action bindings under the project type check; nothing here runs."""

from collections.abc import Awaitable, Callable
from typing import assert_type

from squid_ui.handlers import _HandlerBinder
from squid_ui.interactions import ActionBinding, PressEvent, SelectionEvent


async def press(_event: PressEvent) -> None: ...


async def select(_event: SelectionEvent) -> None: ...


assert_type(ActionBinding("press", press), ActionBinding[PressEvent])
assert_type(ActionBinding("select", select), ActionBinding[SelectionEvent])


async def native_press(_event: PressEvent[str]) -> None: ...


assert_type(_HandlerBinder("native").callback(native_press), Callable[[PressEvent[str]], Awaitable[None]])
assert_type(_HandlerBinder("native").action(ActionBinding("native", native_press)), ActionBinding[PressEvent[str]])
