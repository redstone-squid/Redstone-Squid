"""One navigation factory for materialized and asynchronously loaded cursors."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from squid_layouts.chrome import Chrome
from squid_layouts.primitives.nodes import Button, Gallery, RawItem, RoutedSelect, Row, SelectMenu, Sep, Thumbnail
from squid_layouts.runtime.context import ContextKey
from squid_layouts.sources import CountPrecision, Position
from squid_layouts.text import TextLike

MATERIALIZED_PREVIOUS_KEY = "__cursor_previous"
MATERIALIZED_NEXT_KEY = "__cursor_next"


@dataclass(frozen=True, slots=True)
class NavigationState:
    """Proven cursor facts with no interaction handlers."""

    key: str
    position: Position
    has_previous: bool
    has_next: bool
    backward: bool
    previous_label: TextLike
    next_label: TextLike
    previous_key: str
    next_key: str
    extent: int | None = None
    visible_range: tuple[int, int] | None = None
    total: int | None = None
    count: CountPrecision = CountPrecision.NONE


@dataclass(frozen=True, slots=True)
class NavigationContext:
    """Cursor facts and the handlers a navigation factory can bind."""

    state: NavigationState
    on_previous: Callable[..., Awaitable[None]]
    on_next: Callable[..., Awaitable[None]]

    @property
    def key(self) -> str:
        return self.state.key

    @property
    def position(self) -> Position:
        return self.state.position

    @property
    def has_previous(self) -> bool:
        return self.state.has_previous

    @property
    def has_next(self) -> bool:
        return self.state.has_next


type NavNode = Row | SelectMenu | RoutedSelect | Sep | Thumbnail | Gallery | RawItem
type NavFactory = Callable[[NavigationContext], Sequence[NavNode]]
type PlannedNav = Callable[[NavigationState], Sequence[NavNode]]

NAV_FACTORY_CONTEXT = ContextKey[NavFactory]("nav_factory")


def navigation_controls(context: NavigationContext) -> Row:
    """Build stable boundary-aware controls from the facts in ``context``."""
    state = context.state
    previous = (
        (
            Button(
                label=state.previous_label,
                on_click=context.on_previous,
                key=state.previous_key,
                disabled=not state.has_previous,
            ),
        )
        if state.backward
        else ()
    )
    return Row(
        (
            *previous,
            Button(
                label=state.next_label,
                on_click=context.on_next,
                key=state.next_key,
                disabled=not state.has_next,
            ),
        )
    )


def default_nav(context: NavigationContext) -> Sequence[NavNode]:
    """The stock factory shared by materialized pages and source windows."""
    return (navigation_controls(context),)


def materialized_navigation_state(key: str, position: Position, extent: int, chrome: Chrome) -> NavigationState:
    """Build the navigation facts shared by every materialized slicer."""
    return NavigationState(
        key=key,
        position=position,
        has_previous=position.offset > 0,
        has_next=position.offset < extent - 1,
        backward=True,
        previous_label=chrome.previous,
        next_label=chrome.next,
        previous_key=f"{MATERIALIZED_PREVIOUS_KEY}.{key}",
        next_key=f"{MATERIALIZED_NEXT_KEY}.{key}",
        extent=extent,
    )


__all__ = [
    "MATERIALIZED_NEXT_KEY",
    "MATERIALIZED_PREVIOUS_KEY",
    "NAV_FACTORY_CONTEXT",
    "NavFactory",
    "NavNode",
    "NavigationContext",
    "NavigationState",
    "PlannedNav",
    "default_nav",
    "materialized_navigation_state",
    "navigation_controls",
]
