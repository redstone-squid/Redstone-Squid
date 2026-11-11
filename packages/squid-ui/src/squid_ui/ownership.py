"""Choose whether the application or the UI runtime keeps a control's value.

Stateful controls such as choices, toggles and pagers take an `Ownership` value. With
`Uncontrolled`, the runtime stores the value in `PresentationState` under the control's key.
With `Controlled`, the application passes the value on every render and receives requested
changes as events. This module sits below both `semantic` and `primitives`, so semantic nodes
and primitive overflow policies such as `Paginate` share one definition.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from squid_ui.handlers import _HandlerBinder
from squid_ui.interactions import ActionEvent


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


@dataclass(frozen=True, slots=True)
class PageEvent[ContextT = object](ActionEvent[ContextT]):
    """The reader asked a controlled pager to show another page."""

    page: int = 0
    """The requested zero-based page, already clamped to the pages that exist."""


@dataclass(frozen=True, slots=True)
class RoutedPage:
    """An application-owned page whose navigation uses persistent route IDs.

    `route` receives a clamped zero-based target page. `seek_route`, when supplied,
    returns one input route for a jump menu whose submitted value is the target page.
    The host validates input and owns authorization, state persistence, and redraws.

    Planning may call these factories repeatedly, including for discarded candidates.
    They must be safe to repeat; prefer deterministic encoding without registration.
    Only the returned IDs go into the scene, so no Python callback survives delivery.
    """

    value: int
    route: Callable[[int], str]
    seek_route: Callable[[], str] | None = None


type PageOwnership[ContextT = object] = Ownership[int, PageEvent[ContextT]] | RoutedPage
"""Who keeps a pager's zero-based page.

A negative page counts from the end, as Python indexing does: `-1` is the last page. The
planner clamps any value to the pages the content actually has, because a pager's page
count is only known after layout. `RoutedPage` supplies an application-owned value
with persistent navigation routes instead of an `on_change` callback.
"""

FIRST_PAGE: PageOwnership = Uncontrolled(0)
LAST_PAGE: PageOwnership = Uncontrolled(-1)
"""Open on the final page, for content read from the bottom such as a traceback or log."""


def resolve_page(page: int, extent: int) -> int:
    """Clamp a requested page, counting negative pages from the end, to ``extent`` pages."""
    if page < 0:
        page += extent
    return max(0, min(page, extent - 1))


__all__ = [
    "FIRST_PAGE",
    "LAST_PAGE",
    "Controlled",
    "Ownership",
    "PageEvent",
    "PageOwnership",
    "RoutedPage",
    "Uncontrolled",
    "bind_ownership",
    "resolve_page",
]
