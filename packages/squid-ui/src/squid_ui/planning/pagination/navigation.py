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
from dataclasses import field as dataclass_field
from typing import Any, Self, cast, override

from squid_ui.authoring import semantic as sem
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.text import TextLike
from squid_ui.context import ContextKey
from squid_ui.interactions import ActionEvent, ActionMode, PressEvent, SelectionEvent, SubmitEvent
from squid_ui.interactions.forms import FormBinding, FormSpec, IntField
from squid_ui.interactions.handlers import _HandlerBinder, _HandlerComposition
from squid_ui.interactions.ownership import Controlled, Ownership, Uncontrolled, bind_ownership
from squid_ui.overflow import FIRST_PAGE, PageEvent, PageOwnership, RoutedPage
from squid_ui.pages import PagePosition
from squid_ui.presentation_state import PresentationState
from squid_ui.sources import CountPrecision
from squid_ui.target_types import Renderable

MATERIALIZED_PREVIOUS_KEY = "__cursor_previous"
MATERIALIZED_NEXT_KEY = "__cursor_next"
MATERIALIZED_SEEK_KEY = "__cursor_seek"


@dataclass(frozen=True, slots=True)
class PageCommit(_HandlerComposition):
    """Send a page turn on one materialized pager to whoever owns its page.

    Shared pagination stages an uncontrolled page request until delivery commits
    the display; legacy pagination moves the cursor immediately. A controlled page
    goes to the application's `on_change`, which decides whether to accept it.
    """

    owner: Ownership[int, PageEvent]
    key: str
    extent: int
    session: PresentationState
    staged: bool = False

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
                if self.staged:
                    self.session.request_cursor(self.key, PagePosition(page=page))
                else:
                    self.session.move_cursor(self.key, PagePosition(page=page))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class TurnPage(_HandlerComposition):
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

    async def __call__(self, event: PressEvent) -> None:
        await self.commit(event, self.page)


@dataclass(frozen=True, slots=True)
class SeekPageSelection(_HandlerComposition):
    seek: Callable[[ActionEvent, int], Awaitable[None]]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        # A component's own seek method needs no binding; a pager's commit may hold a
        # frontend-specific on_change.
        if isinstance(self.seek, PageCommit):
            seek = binder.composition(self.seek)
            return replace(self, seek=seek) if seek is not self.seek else self
        return self

    async def __call__(self, event: SelectionEvent) -> None:
        if event.values:
            await self.seek(event, int(event.values[0]))


@dataclass(frozen=True, slots=True)
class SubmitPageJump(_HandlerComposition):
    seek: Callable[[ActionEvent, int], Awaitable[None]]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        if isinstance(self.seek, _HandlerComposition):
            seek = binder.composition(self.seek)
            return replace(self, seek=seek) if seek is not self.seek else self
        return self

    async def __call__(self, event: SubmitEvent) -> None:
        await self.seek(event, cast(int, event.values["page"]) - 1)


@dataclass(frozen=True, slots=True)
class PresentPageForm(_HandlerComposition):
    form: FormBinding

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        form = binder.form(self.form)
        return replace(self, form=form) if form is not self.form else self

    async def __call__(self, event: PressEvent) -> None:
        await event.present_form(
            self.form.spec,
            key=self.form.key,
            on_submit=self.form.on_submit,
            mode=self.form.mode,
            label=self.form.label,
        )


@dataclass(frozen=True, slots=True)
class NavigationState:
    """Proven cursor facts with no interaction handlers."""

    key: str
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
    """Zero-based page number, when known; source addresses remain with their owner."""
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

    chrome: Chrome = dataclass_field(default=DEFAULT_CHROME, kw_only=True)
    jump_route: str | None = None
    """Persistent button route that opens a page-number form, when provided by the host."""

    @property
    def key(self) -> str:
        return self.state.key

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


type NavNode = Renderable[Any]
"""Portable or target-owned navigation supplied by a host."""
type NavFactory = Callable[[NavigationContext], Sequence[NavNode]]
type PlannedNav = Callable[[NavigationContext], Sequence[NavNode]]
"""A navigation factory used during planning, which may also return V2 structures."""

NAV_FACTORY_CONTEXT = ContextKey[NavFactory]("nav_factory")


def page_jump_form(
    page: int, pages: int, *, title: TextLike | None = None, chrome: Chrome = DEFAULT_CHROME
) -> FormSpec:
    """Ask for a 1-based page number; ``page`` is the zero-based current page.

    Routed hosts can present this schema and pass the parsed ``page`` minus one
    to their existing seek transition. Revalidate against current data on submit.
    """
    if pages < 1 or not 0 <= page < pages:
        msg = "page must address one of the available pages"
        raise ValueError(msg)
    return FormSpec(
        chrome.jump_to_page if title is None else title,
        (
            IntField(
                key="page",
                chrome=chrome,
                label=chrome.page_jump_number,
                description=chrome.page_jump_range(pages),
                minimum=1,
                maximum=pages,
                default=page + 1,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class SubmitPageSize(_HandlerComposition):
    change: Callable[[ActionEvent, int], Awaitable[None]]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        if isinstance(self.change, _HandlerComposition):
            change = binder.composition(self.change)
            return replace(self, change=change) if change is not self.change else self
        return self

    async def __call__(self, event: SubmitEvent) -> None:
        await self.change(event, cast(int, event.values["page_size"]))


def page_size_form(page_size: int, *, maximum: int | None = None, chrome: Chrome = DEFAULT_CHROME) -> FormSpec:
    """Ask for a positive item count, optionally bounded by the widget's capacity."""
    if page_size < 1 or (maximum is not None and page_size > maximum):
        message = "page_size must be positive and no greater than maximum"
        raise ValueError(message)
    return FormSpec(
        chrome.items_per_page,
        (
            IntField(
                key="page_size",
                label=chrome.items_per_page,
                minimum=1,
                maximum=maximum,
                default=page_size,
                chrome=chrome,
            ),
        ),
    )


def materialized_navigation_state(key: str, position: PagePosition, extent: int, chrome: Chrome) -> NavigationState:
    """Build the navigation facts shared by every materialized slicer."""
    return NavigationState(
        key=key,
        has_previous=position.page > 0,
        has_next=position.page < extent - 1,
        backward=True,
        previous_label=chrome.previous,
        next_label=chrome.next,
        previous_key=f"{MATERIALIZED_PREVIOUS_KEY}.{key}",
        next_key=f"{MATERIALIZED_NEXT_KEY}.{key}",
        extent=extent,
        page=position.page,
        seek_key=f"{MATERIALIZED_SEEK_KEY}.{key}",
        seek_label=chrome.jump_to_page,
        page_option=chrome.page_option,
    )


@dataclass(frozen=True, slots=True)
class PagerNav:
    """Build the navigation controls for materialized pagers in one plan."""

    factory: PlannedNav
    session: PresentationState
    staged: bool = False

    def __call__(
        self, key: str, page: int, extent: int, chrome: Chrome, owner: PageOwnership = FIRST_PAGE
    ) -> Sequence[NavNode]:
        """Return controls for ``page`` of ``extent`` that send page turns to ``owner``."""
        state = materialized_navigation_state(key, PagePosition(page=page), extent, chrome)
        if isinstance(owner, RoutedPage):
            return self.factory(
                NavigationContext(
                    state,
                    owner.route(max(0, page - 1)),
                    owner.route(min(extent - 1, page + 1)),
                    owner.seek_route() if owner.seek_route is not None else None,
                    owner.jump_route(page, extent) if owner.jump_route is not None else None,
                    chrome=chrome,
                )
            )
        commit = PageCommit(owner, key, extent, self.session, self.staged)
        return self.factory(
            NavigationContext(state, TurnPage(commit, page - 1), TurnPage(commit, page + 1), commit, chrome=chrome)
        )


__all__ = [
    "MATERIALIZED_NEXT_KEY",
    "MATERIALIZED_PREVIOUS_KEY",
    "MATERIALIZED_SEEK_KEY",
    "NAV_FACTORY_CONTEXT",
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
    "page_jump_form",
    "page_select_nav",
    "page_size_button",
    "page_size_form",
]


def default_nav(context: NavigationContext) -> Sequence[NavNode]:
    """Build portable Previous and Next actions from navigation state."""
    state = context.state

    def control(
        label: TextLike, binding: Callable[..., Awaitable[None]] | str, key: str, available: bool
    ) -> sem.ActionControl | sem.RoutedActionControl:
        return (
            sem.RoutedActionControl(key, label, binding, available=available)
            if isinstance(binding, str)
            else sem.ActionControl(key, label, binding, available=available)
        )

    previous = (
        (control(state.previous_label, context.on_previous, state.previous_key, state.has_previous),)
        if state.backward
        else ()
    )
    return (
        sem.ActionControls(
            (*previous, control(state.next_label, context.on_next, state.next_key, state.has_next)),
            key=f"{state.next_key}.navigation",
        ),
    )


def page_size_button(
    key: str,
    page_size: int,
    on_change: Callable[[ActionEvent, int], Awaitable[None]],
    *,
    maximum: int | None = None,
    chrome: Chrome = DEFAULT_CHROME,
) -> sem.FormTrigger:
    """Offer a portable form that changes an application's items-per-page value."""
    return sem.FormTrigger(
        f"{key}.page-size",
        chrome.items_per_page,
        page_size_form(page_size, maximum=maximum, chrome=chrome),
        SubmitPageSize(on_change),
        mode=ActionMode.REBASE,
    )


@dataclass(frozen=True, slots=True)
class _SeekChoice(_HandlerComposition):
    seek: Callable[[ActionEvent, int], Awaitable[None]]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        if isinstance(self.seek, _HandlerComposition):
            seek = binder.composition(self.seek)
            return replace(self, seek=seek) if seek is not self.seek else self
        return self

    async def __call__(self, event: sem.ChoiceEvent) -> None:
        if event.selected:
            await self.seek(event, int(event.selected[0]))


def page_select_nav(context: NavigationContext) -> Sequence[NavNode]:
    """Previous/Next plus portable page choices when random access is available."""
    state, seek = context.state, context.on_seek
    if seek is None or state.page is None or state.extent is None or state.extent < 2 or state.page_option is None:
        return default_nav(context)
    choices = tuple(sem.Choice(str(page), state.page_option(page + 1)) for page in range(state.extent))
    picker = (
        sem.RoutedChoices(
            state.seek_key, choices, seek, placeholder=state.seek_label or None, selected=(str(state.page),)
        )
        if isinstance(seek, str)
        else sem.Choices(state.seek_key, choices, selection=Controlled((str(state.page),), _SeekChoice(seek)))
    )
    return (*default_nav(context), picker)
