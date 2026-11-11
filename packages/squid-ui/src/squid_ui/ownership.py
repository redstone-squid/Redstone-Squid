"""Choose whether the application or the UI runtime keeps a control's value.

Stateful controls such as choices, toggles and pagers take an `Ownership` value. With
`Uncontrolled`, the runtime stores the value in `PresentationState` under the control's key.
With `Controlled`, the application passes the value on every render and receives requested
changes as events. This module sits below both `semantic` and `primitives`, so semantic nodes
and primitive overflow policies such as `Paginate` share one definition. Page-specific values
live beside `Paginate` in `squid_ui.primitives.constraints`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from squid_ui.handlers import _HandlerBinder


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


type Ownership[ValueT, EventT] = Controlled[ValueT, EventT] | Uncontrolled[ValueT]
"""Explicitly choose author-owned values or state stored in the presentation session."""


def bind_ownership[ValueT, EventT](
    ownership: Ownership[ValueT, EventT], binder: _HandlerBinder
) -> Ownership[ValueT, EventT]:
    """Resolve a controlled callback for the frontend that is binding the plan."""

    def bind() -> Ownership[ValueT, EventT]:
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
]
