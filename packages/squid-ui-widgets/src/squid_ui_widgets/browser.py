"""Present a browsing session as an overview or focused detail."""

from collections.abc import Callable, Sequence
from functools import partial
from typing import override

from squid_reactivity import untracked
from squid_reactivity.operations import Failed, Pending
from squid_reactivity.resources import PendingMode, resource
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.document import DocumentLike
from squid_ui.factories import (
    action_control,
    action_controls,
    bullet,
    bullets,
    choice,
    choices,
    controlled,
    heading,
    note,
    stack,
)
from squid_ui.interactions import ActionEvent
from squid_ui.pages import RevealPage
from squid_ui.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    MountNavNode,
    NavigationContext,
    default_nav,
)
from squid_ui.primitives.constraints import FIRST_PAGE
from squid_ui.runtime.component import Component
from squid_ui.semantic import ActionControl, ChoiceEvent, ControlDisplay, Link
from squid_ui.sources import (
    Chronology,
    LoadedWindow,
    Position,
    WindowSource,
    window_footer,
)
from squid_ui.target_types import DiscordTarget
from squid_ui.text import Text, TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    source_navigation_state,
    step_labels,
)
from squid_ui_widgets.browsing import BrowseSession, Movement

type BrowserDetail[ItemT, RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object] = Callable[
    [ItemT], ContentLike[RenderTargetT, ContextT]
]
type BrowserOverview[ItemT, RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object] = Callable[
    [LoadedWindow[ItemT]], ContentLike[RenderTargetT, ContextT]
]


class Browser[ItemT, RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object](
    Component[RenderTargetT, ContextT]
):
    """Browse a window source and open one item's detail content.

    The session owns source navigation and focus. Detail content is prepared through
    an atomic resource when focus changes; the factory must be safe to retry. Refreshing
    the same item retains its detail, whose component owns refresh of its domain data.

    Paging and stepping between opened items say Previous and Next. Pass `chronology` for a
    list sorted by time to label them Newer and Older instead.
    """

    def __init__(
        self,
        source: WindowSource[ItemT] | BrowseSession[ItemT],
        *,
        key: str = "browser",
        identity: Callable[[ItemT], str] | None = None,
        label: Callable[[ItemT], TextLike],
        detail: BrowserDetail[ItemT, RenderTargetT, ContextT],
        summary: Callable[[ItemT], TextLike] | None = None,
        entry_actions: Callable[[ItemT], Sequence[ActionControl | Link]] | None = None,
        overview: BrowserOverview[ItemT, RenderTargetT, ContextT] | None = None,
        page_size: int = 10,
        title: TextLike | None = None,
        empty: ContentLike[RenderTargetT, ContextT] = "No entries",
        copy: LoadingCopy = DEFAULT_LOADING_COPY,
        chronology: Chronology | None = None,
    ) -> None:
        title = None if title is None else Text(title)
        self.key = require_key(key, name="Browser.key")
        if isinstance(source, BrowseSession):
            self.session = source
        else:
            if identity is None:
                message = "Browser requires identity when given a source"
                raise ValueError(message)
            self.session = BrowseSession(source, identity=identity, page_size=page_size)
        if not 1 <= self.session.page_size <= 25:
            message = "Browser.page_size must be between 1 and 25"
            raise ValueError(message)
        self.identity = self.session.identity
        self.label = label
        self.detail = detail
        self.summary = summary
        self.entry_actions = entry_actions
        self.overview = overview
        self.page_size = self.session.page_size
        self.title = title
        self.empty = normalize_content(empty, name="Browser.empty")
        self.copy = copy
        self.chronology = chronology

    @property
    def source(self) -> WindowSource[ItemT]:
        return self.session.source

    @property
    def opened(self) -> ItemT | None:
        return self.session.focused_item

    @resource(pending=PendingMode.ATOMIC)
    async def _detail(self) -> ContentLike[RenderTargetT, ContextT] | None:
        # Only a focus change replaces the child. Refreshing the same item leaves its
        # editor state intact; the child owns refresh of its own domain data.
        _visit = self.session.visit
        key = self.session.focus
        with untracked():
            item = self.session.focused_item
        return self.detail(item) if key is not None and item is not None else None

    async def refresh(self) -> None:
        self.session.move("refresh", revision=self.session.revision)
        if self.session.operation is not None:
            await self.session.operation

    async def _move(self, movement: Movement, revision: int, _event: ActionEvent) -> None:
        self.session.move(movement, revision=revision)

    async def _seek(self, revision: int, _event: ActionEvent, page: int) -> None:
        self.session.move("seek", revision=revision, position=Position(offset=page * self.page_size))

    async def _retry(self, revision: int, _event: ActionEvent) -> None:
        self.session.retry(revision=revision)

    async def _selected(self, revision: int, event: ChoiceEvent) -> None:
        if len(event.selected) == 1:
            self.session.open(event.selected[0], revision=revision)

    async def _back(self, _event: ActionEvent) -> None:
        self.session.back(revision=self.session.revision)

    async def _back_at(self, revision: int, _event: ActionEvent) -> None:
        self.session.back(revision=revision)

    @override
    def render(self) -> DocumentLike[RenderTargetT, ContextT]:
        current = self.session.snapshot
        operation = self.session.operation
        status = None if operation is None else operation.status
        failed = isinstance(status, Failed)
        status_text = self.copy.failed if failed else self.copy.loading if isinstance(status, Pending) else None
        if current is None:
            return self._status(status_text or self.copy.loading, retry=failed)
        if current.notice == "removed":
            status_text = self.copy.removed
        elif current.notice == "end":
            status_text = self.copy.end
        if current.focus is not None:
            item = self.session.focused_item
            assert item is not None
            return self._render_detail(current.loaded, item, status_text=status_text, retry=failed)
        return self._render_overview(current.loaded, status_text=status_text, retry=failed)

    def _status(self, message: TextLike, *, retry: bool = False) -> DocumentLike[RenderTargetT, ContextT]:
        """Render a source status without a stale window."""
        return stack(
            heading(self.title) if self.title is not None else None,
            note(message),
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.retry-row",
            )
            if retry
            else None,
        )

    def _render_overview(
        self,
        loaded: LoadedWindow[ItemT],
        *,
        status_text: TextLike | None,
        retry: bool,
    ) -> DocumentLike[RenderTargetT, ContextT]:
        """Render overview content, item selection, and navigation."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        items = loaded.window.items
        extra = normalize_content(self.overview(loaded), name="Browser.overview") if self.overview is not None else ()
        if items:
            listing = (
                bullets(
                    *(
                        bullet(
                            self.summary(item) if self.summary is not None else self.label(item),
                            key=self.identity(item),
                        )
                        for item in items
                    ),
                    key=f"{self.key}.items",
                    page=RevealPage(anchor=current.reveal, request=str(self.session.visit))
                    if (current := self.session.snapshot) is not None and current.reveal is not None
                    else FIRST_PAGE,
                ),
            )
            picker = choices(
                *(choice(self.label(item), key=self.identity(item)) for item in items),
                key=f"{self.key}.open",
                selection=controlled((), partial(self._selected, self.session.revision)),
                minimum=1,
                maximum=1,
            )
        else:
            listing = self.empty
            picker = None
        return stack(
            heading(self.title) if self.title is not None else None,
            *extra,
            *listing,
            picker,
            *self._navigation(loaded),
            note(status_text) if status_text is not None else None,
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.retry-row",
            )
            if retry
            else None,
            note(footer) if (footer := window_footer(chrome, self.source, loaded, self.page_size)) else None,
        )

    def _navigation(self, loaded: LoadedWindow[ItemT]) -> tuple[MountNavNode, ...]:
        """Bind shared source navigation facts to mounted handlers."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nav = self.inject(NAV_FACTORY_CONTEXT, default_nav)
        state = source_navigation_state(
            loaded,
            self.source.capabilities,
            key=self.key,
            page_size=self.page_size,
            chrome=chrome,
            chronology=self.chronology,
        )
        if state is None:
            return ()
        return tuple(
            nav(
                NavigationContext(
                    state,
                    partial(self._move, "previous", self.session.revision),
                    partial(self._move, "next", self.session.revision),
                    partial(self._seek, self.session.revision) if state.extent is not None else None,
                )
            )
        )

    def _render_detail(
        self,
        loaded: LoadedWindow[ItemT],
        item: ItemT,
        *,
        status_text: TextLike | None,
        retry: bool,
    ) -> DocumentLike[RenderTargetT, ContextT]:
        """Render one opened item and its local actions."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        items = loaded.window.items
        detail_value = self._detail.value
        assert detail_value is not None
        detail = normalize_content(detail_value, name="Browser.detail")
        previous_label, next_label = step_labels(chrome, self.chronology)
        entry_actions = tuple(self.entry_actions(item)) if self.entry_actions is not None else ()
        return stack(
            heading(self.title) if self.title is not None else None,
            *detail,
            action_controls(*entry_actions, key=f"{self.key}.entry-actions") if entry_actions else None,
            action_controls(
                (len(items) > 1 or loaded.window.has_previous or loaded.window.has_next)
                and action_control(
                    previous_label,
                    partial(self._move, "previous-item", self.session.revision),
                    key=f"{self.key}.item-previous",
                    available=self.session.can_step(-1) and not self.session.busy,
                ),
                (len(items) > 1 or loaded.window.has_previous or loaded.window.has_next)
                and action_control(
                    next_label,
                    partial(self._move, "next-item", self.session.revision),
                    key=f"{self.key}.item-next",
                    available=self.session.can_step(1) and not self.session.busy,
                ),
                action_control(chrome.back, partial(self._back_at, self.session.revision), key=f"{self.key}.back"),
                key=f"{self.key}.detail-navigation",
                display=ControlDisplay.INDIVIDUAL,
            ),
            note(status_text) if status_text is not None else None,
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.retry-row",
            )
            if retry
            else None,
        )


__all__ = ["Browser", "BrowserDetail", "BrowserOverview"]
