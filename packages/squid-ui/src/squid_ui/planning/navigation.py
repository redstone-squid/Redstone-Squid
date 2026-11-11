"""Build navigation controls for complete content and asynchronously loaded windows.

Both kinds of pager expose `NavigationState`, which describes the visible position
and available movements. `NavigationContext` adds handlers so a factory can build
buttons or a jump menu without knowing how the content is loaded. Page numbers
are separate from cursor offsets: a source cursor may address items, not pages.

A materialized pager is one whose content the planner already holds, such as a
`Paginate` text pager or an option window. `PagerNav` builds its controls during
planning, and `PageCommit` sends each page turn to whoever owns the page: the
presentation session, or the application through `Controlled.on_change`.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Self, override

from squid_ui.chrome import Chrome
from squid_ui.context import ContextKey
from squid_ui.handlers import _HandlerBinder, _HandlerComposition
from squid_ui.interactions import ActionEvent, PressEvent, SelectionEvent
from squid_ui.ownership import Controlled, Ownership, Uncontrolled, bind_ownership
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.presentation import PresentationState
from squid_ui.primitives.constraints import FIRST_PAGE, PageEvent, PageOwnership, RoutedPage
from squid_ui.primitives.nodes import (
    Button,
    Gallery,
    Option,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Thumbnail,
)
from squid_ui.sources import CountPrecision, Position
from squid_ui.text import TextLike

MATERIALIZED_PREVIOUS_KEY = "__cursor_previous"
MATERIALIZED_NEXT_KEY = "__cursor_next"
MATERIALIZED_SEEK_KEY = "__cursor_seek"

SEEK_OPTION_LIMIT = 25
"""Options one jump select may carry -- Discord's per-menu maximum."""


@dataclass(frozen=True, slots=True)
class PageCommit(_HandlerComposition):
    """Send a page turn on one materialized pager to whoever owns its page.

    An uncontrolled page moves the pager's cursor in the presentation session and
    redraws. A controlled page goes to the application's `on_change`, which decides
    whether to accept it.
    """

    owner: Ownership[int, PageEvent]
    key: str
    extent: int
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        owner = bind_ownership(self.owner, binder)
        return replace(self, owner=owner) if owner is not self.owner else self

    async def __call__(self, event: ActionEvent, page: int) -> None:
        page = max(0, min(page, self.extent - 1))
        match self.owner:
            case Controlled(on_change=on_change):
                await on_change(
                    PageEvent(event.actor, event.responder, event.locale, event.metadata, page, context=event.context)
                )
            case Uncontrolled():
                # No explicit acknowledgement: the frontend answers the press by editing the
                # message in place, so a page turn costs one response instead of two.
                self.session.move_cursor(self.key, Position(offset=page))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class TurnPage(GeneratedHandler[PressEvent]):
    """Move to a page fixed when the button was drawn.

    Previous and Next step from the page on screen, not from the stored cursor, so a
    press always means "the page next to the one the reader sees".
    """

    commit: PageCommit
    page: int

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: PressEvent) -> None:
        await self.commit(event, self.page)


@dataclass(frozen=True, slots=True)
class _SeekSelection(GeneratedHandler[SelectionEvent]):
    seek: Callable[[ActionEvent, int], Awaitable[None]]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        # A component's own seek method needs no binding; a pager's commit may hold a
        # frontend-specific on_change.
        if isinstance(self.seek, PageCommit):
            seek = binder.composition(self.seek)
            return replace(self, seek=seek) if seek is not self.seek else self
        return self

    @override
    async def __call__(self, event: SelectionEvent) -> None:
        if event.values:
            await self.seek(event, int(event.values[0]))


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
    """Cursor facts and navigation bindings: Python callbacks or persistent route IDs."""

    state: NavigationState
    on_previous: Callable[..., Awaitable[None]] | str
    on_next: Callable[..., Awaitable[None]] | str
    on_seek: Callable[[ActionEvent, int], Awaitable[None]] | str | None = None
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
type MountNavNode = Row | SelectMenu | RoutedSelect | RawItem
"""Navigation a mount may author before its Discord message mode is selected.

`NavNode` also permits V2 structures for planner factories that already know the mode.
"""
type NavFactory = Callable[[NavigationContext], Sequence[MountNavNode]]
type PlannedNav = Callable[[NavigationContext], Sequence[NavNode]]
"""A navigation factory used during planning, which may also return V2 structures."""

NAV_FACTORY_CONTEXT = ContextKey[NavFactory]("nav_factory")


def navigation_controls(context: NavigationContext) -> Row:
    """Build stable boundary-aware controls from the facts in ``context``."""
    state = context.state

    def button(
        label: TextLike, binding: Callable[..., Awaitable[None]] | str, key: str, available: bool
    ) -> Button | RoutedButton:
        if isinstance(binding, str):
            return RoutedButton(label, binding, disabled=not available)
        return Button(label=label, on_click=binding, key=key, disabled=not available)

    previous = (
        (button(state.previous_label, context.on_previous, state.previous_key, state.has_previous),)
        if state.backward
        else ()
    )
    return Row((*previous, button(state.next_label, context.on_next, state.next_key, state.has_next)))


def default_nav(context: NavigationContext) -> Sequence[MountNavNode]:
    """The stock factory shared by materialized pages and source windows."""
    return (navigation_controls(context),)


def _seek_pages(page: int, extent: int) -> list[int]:
    """Which zero-based pages a jump select should offer, always including ``page``."""
    if extent <= SEEK_OPTION_LIMIT:
        return list(range(extent))
    # Spread jump choices across the whole range so distant pages remain accessible.
    step = (extent - 1) / (SEEK_OPTION_LIMIT - 1)
    ladder = {round(index * step) for index in range(SEEK_OPTION_LIMIT)}
    if page not in ladder:
        # Trade the nearest rung for the visible page, so the select can show it as selected
        # without growing past the option limit. The ends stay: they are the whole range.
        ladder.discard(min(ladder - {0, extent - 1}, key=lambda rung: abs(rung - page)))
        ladder.add(page)
    return sorted(ladder)


def seek_control(context: NavigationContext) -> SelectMenu | RoutedSelect | None:
    """A jump select for a cursor that can address a page, or `None` when it cannot."""
    state = context.state
    seek = context.on_seek
    extent, page = state.extent, state.page
    if seek is None or extent is None or page is None or extent < 2 or state.page_option is None:
        return None
    label = state.page_option

    options = tuple(
        Option(label(number + 1), str(number), default=number == page) for number in _seek_pages(page, extent)
    )
    if isinstance(seek, str):
        return RoutedSelect(options, seek, placeholder=state.seek_label or None)
    return SelectMenu(options, on_select=_SeekSelection(seek), key=state.seek_key, placeholder=state.seek_label or None)


def page_select_nav(context: NavigationContext) -> Sequence[MountNavNode]:
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


@dataclass(frozen=True, slots=True)
class PagerNav:
    """Build the navigation controls for materialized pagers in one plan."""

    factory: PlannedNav
    session: PresentationState

    def __call__(
        self, key: str, page: int, extent: int, chrome: Chrome, owner: PageOwnership = FIRST_PAGE
    ) -> Sequence[NavNode]:
        """Return controls for ``page`` of ``extent`` that send page turns to ``owner``."""
        state = materialized_navigation_state(key, Position(offset=page), extent, chrome)
        if isinstance(owner, RoutedPage):
            return self.factory(
                NavigationContext(
                    state,
                    owner.route(max(0, page - 1)),
                    owner.route(min(extent - 1, page + 1)),
                    owner.seek_route() if owner.seek_route is not None else None,
                )
            )
        commit = PageCommit(owner, key, extent, self.session)
        return self.factory(NavigationContext(state, TurnPage(commit, page - 1), TurnPage(commit, page + 1), commit))


__all__ = [
    "MATERIALIZED_NEXT_KEY",
    "MATERIALIZED_PREVIOUS_KEY",
    "MATERIALIZED_SEEK_KEY",
    "NAV_FACTORY_CONTEXT",
    "SEEK_OPTION_LIMIT",
    "MountNavNode",
    "NavFactory",
    "NavNode",
    "NavigationContext",
    "NavigationState",
    "PageCommit",
    "PagerNav",
    "PlannedNav",
    "TurnPage",
    "default_nav",
    "materialized_navigation_state",
    "navigation_controls",
    "page_select_nav",
    "seek_control",
]
