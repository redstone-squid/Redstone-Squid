"""One navigation factory for materialized and asynchronously loaded cursors."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from squid_ui.chrome import Chrome
from squid_ui.interactions import SelectionEvent
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.primitives.nodes import (
    Button,
    Gallery,
    Option,
    RawItem,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Thumbnail,
)
from squid_ui.runtime.context import ContextKey
from squid_ui.sources import CountPrecision, Position
from squid_ui.text import TextLike

MATERIALIZED_PREVIOUS_KEY = "__cursor_previous"
MATERIALIZED_NEXT_KEY = "__cursor_next"
MATERIALIZED_SEEK_KEY = "__cursor_seek"

SEEK_OPTION_LIMIT = 25
"""Options one jump select may carry -- Discord's per-menu maximum."""


@dataclass(frozen=True, slots=True)
class _SeekSelection(GeneratedHandler[SelectionEvent]):
    seek: Callable[[int], Awaitable[None]]

    async def __call__(self, event: SelectionEvent) -> None:
        if event.values:
            await self.seek(int(event.values[0]))


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
    """How many pages there are, when that is known."""
    page: int | None = None
    """Which page is visible, zero-based.

    Not `position.offset`: that is a page index for a materialized cursor but an *item*
    offset for a source window, so only this is comparable against `extent`.
    """
    visible_range: tuple[int, int] | None = None
    total: int | None = None
    count: CountPrecision = CountPrecision.NONE
    seek_key: str = ""
    seek_label: TextLike = ""
    """Placeholder for a jump control, resolved from chrome by whoever built this."""
    page_option: Callable[[int], TextLike] | None = None
    """Labels one jump entry, called with a 1-based page number."""


@dataclass(frozen=True, slots=True)
class NavigationContext:
    """Cursor facts and the handlers a navigation factory can bind."""

    state: NavigationState
    on_previous: Callable[..., Awaitable[None]]
    on_next: Callable[..., Awaitable[None]]
    on_seek: Callable[[int], Awaitable[None]] | None = None
    """Jump to a zero-based page, or `None` when the source cannot address one.

    A source declares this reachable with `SourceCapabilities.jumpable`; a materialized
    cursor always knows its own extent, so it always offers one.
    """

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

    @property
    def page(self) -> int | None:
        return self.state.page

    @property
    def extent(self) -> int | None:
        return self.state.extent


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


def _seek_pages(page: int, extent: int) -> list[int]:
    """Which zero-based pages a jump select should offer, always including ``page``."""
    if extent <= SEEK_OPTION_LIMIT:
        return list(range(extent))
    # An evenly spaced ladder rather than a window around the reader: two clicks then reach
    # any page in the range, where a local window can only ever crawl.
    step = (extent - 1) / (SEEK_OPTION_LIMIT - 1)
    ladder = {round(index * step) for index in range(SEEK_OPTION_LIMIT)}
    if page not in ladder:
        # Trade the nearest rung for the visible page, so the select can show it as selected
        # without growing past the option limit. The ends stay: they are the whole range.
        ladder.discard(min(ladder - {0, extent - 1}, key=lambda rung: abs(rung - page)))
        ladder.add(page)
    return sorted(ladder)


def seek_control(context: NavigationContext) -> SelectMenu | None:
    """A jump select for a cursor that can address a page, or `None` when it cannot."""
    state = context.state
    seek = context.on_seek
    extent, page = state.extent, state.page
    if seek is None or extent is None or page is None or extent < 2 or state.page_option is None:
        return None
    label = state.page_option

    return SelectMenu(
        options=tuple(
            Option(label(number + 1), str(number), default=number == page) for number in _seek_pages(page, extent)
        ),
        on_select=_SeekSelection(seek),
        key=state.seek_key,
        placeholder=state.seek_label or None,
    )


def page_select_nav(context: NavigationContext) -> Sequence[NavNode]:
    """`default_nav` plus a jump select wherever the cursor can address a page.

    Opt in per mount (`MessageRoot(..., nav=sl.page_select_nav)`): a select is a whole component
    row, and spending one on every paginator in the process is the host's call, not the
    framework's.
    """
    control = seek_control(context)
    return (navigation_controls(context),) if control is None else (navigation_controls(context), control)


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
        page=position.offset,
        seek_key=f"{MATERIALIZED_SEEK_KEY}.{key}",
        seek_label=chrome.jump_to_page,
        page_option=chrome.page_option,
    )


__all__ = [
    "MATERIALIZED_NEXT_KEY",
    "MATERIALIZED_PREVIOUS_KEY",
    "MATERIALIZED_SEEK_KEY",
    "NAV_FACTORY_CONTEXT",
    "SEEK_OPTION_LIMIT",
    "NavFactory",
    "NavNode",
    "NavigationContext",
    "NavigationState",
    "PlannedNav",
    "default_nav",
    "materialized_navigation_state",
    "navigation_controls",
    "page_select_nav",
    "seek_control",
]
