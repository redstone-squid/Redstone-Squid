"""Choose whether the application or the UI runtime keeps a control's value.

Stateful nodes take an `Ownership` value. With `Controlled`, the application passes the value
on every render and receives requested changes as events. A `ValueBinding` stores it in a
presentation cell the component reads. Only layout state such as open details or the current
page can be `Uncontrolled`, stored in `PresentationState` under the node's key; value controls
such as choices and toggles exist to be read, so they always have an owner. This module sits below both `semantic` and `primitives`, so semantic nodes
and primitive overflow policies such as `Paginate` share one definition. Page-specific values
live beside `Paginate` in `squid_ui.overflow`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from squid_ui._presentation import ValueBinding
from squid_ui.interactions.handlers import _HandlerBinder


@dataclass(frozen=True, slots=True)
class Controlled[ValueT, EventT]:
    """Read the value from application state and send requested changes to on_change.

    The handler must update that state if it accepts a change; the supplied value
    remains authoritative on every render.
    """

    value: ValueT
    on_change: Callable[[EventT], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Uncontrolled[ValueT]:
    """Let PresentationState store the value under the node's key.

    The initial value applies whenever that key has no stored value, including after a reset.
    Use Controlled when the author-supplied value must win on every render.
    """

    initial: ValueT


type Ownership[ValueT, EventT] = Controlled[ValueT, EventT] | Uncontrolled[ValueT] | ValueBinding[ValueT]
"""Explicitly choose author-owned values or state stored in the presentation session."""


def value_owner[ValueT, EventT](
    factory: str, value: ValueT | ValueBinding[ValueT], on_change: Callable[[EventT], Awaitable[None]] | None
) -> Controlled[ValueT, EventT] | ValueBinding[ValueT]:
    """Pair a factory's value with its writer: a binding writes itself, a plain value needs on_change."""
    if isinstance(value, ValueBinding):
        if on_change is not None:
            message = f"{factory} got a binding, which already receives changes; drop on_change"
            raise TypeError(message)
        return value
    if on_change is None:
        message = f"{factory} needs on_change or a presentation binding, or nothing could read its value"
        raise TypeError(message)
    return Controlled(value, on_change)


def layout_owner[ValueT, EventT](
    factory: str, value: ValueT | ValueBinding[ValueT], on_change: Callable[[EventT], Awaitable[None]] | None
) -> Ownership[ValueT, EventT]:
    """Like value_owner, except a plain value alone starts runtime-owned layout state."""
    if on_change is None and not isinstance(value, ValueBinding):
        return Uncontrolled(value)
    return value_owner(factory, value, on_change)


def bind_ownership[OwnerT: Controlled[Any, Any] | Uncontrolled[Any] | ValueBinding[Any]](
    ownership: OwnerT, binder: _HandlerBinder
) -> OwnerT:
    """Resolve a controlled callback for the frontend that is binding the plan."""

    def bind() -> OwnerT:
        if isinstance(ownership, Controlled):
            callback = binder.callback(ownership.on_change)
            if callback is not ownership.on_change:
                return replace(ownership, on_change=callback)
        return ownership

    return binder.cached(ownership, bind)


__all__ = [
    "Controlled",
    "Ownership",
    "Uncontrolled",
    "bind_ownership",
    "layout_owner",
    "value_owner",
]
