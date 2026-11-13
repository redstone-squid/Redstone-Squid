"""Search-then-pick interaction for non-platform domain entities."""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import override

from squid_reactivity.core import (
    state,
)
from squid_reactivity.operations import Failed, Pending
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.document import DocumentLike
from squid_ui.factories import (
    ChildLike,
    action_control,
    action_controls,
    choice,
    choices,
    controlled,
    form,
    heading,
    note,
    paragraph,
    stack,
)
from squid_ui.forms import FormSpec, TextField
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.runtime.component import Component
from squid_ui.semantic import ChoiceEvent, ControlDisplay, LayoutNode, Tone
from squid_ui.sources import WindowSource, window_footer
from squid_ui.text import TextLike
from squid_ui_widgets._content import require_key
from squid_ui_widgets._window import LoadingCopy
from squid_ui_widgets.browsing import BrowseSession, BrowseSnapshot, Movement

type SearchProvider[ItemT] = Callable[[str], WindowSource[ItemT]]
type SearchPickHandler[ItemT] = Callable[[ActionEvent, tuple[ItemT, ...]], Awaitable[None]]


SEARCH_LOADING_COPY = LoadingCopy(failed="Could not load results.")
"""Default copy for a search: what failed to load was results, not entries."""


class SearchPicker[ItemT](Component):
    """Search a window source and retain picked items across query changes.

    With maximum=1 a new pick replaces the previous one; otherwise picks accumulate.
    Pick callbacks run after local state changes. The host owns durable persistence.
    """

    query: str | None = state(None)
    picked: tuple[ItemT, ...] = state((), persist=False, opaque=True)
    session: BrowseSession[ItemT] | None = state(None, persist=False, opaque=True)

    def __init__(
        self,
        search: SearchProvider[ItemT],
        *,
        key: str = "lookup",
        identity: Callable[[ItemT], str],
        label: Callable[[ItemT], TextLike],
        description: Callable[[ItemT], TextLike] | None = None,
        minimum: int = 0,
        maximum: int = 1,
        picked: Sequence[ItemT] = (),
        on_pick: SearchPickHandler[ItemT],
        page_size: int = 10,
        copy: LoadingCopy = SEARCH_LOADING_COPY,
    ) -> None:
        self.key = require_key(key, name="SearchPicker.key")
        if minimum < 0 or maximum < 1 or minimum > maximum:
            message = "SearchPicker bounds must satisfy 0 <= minimum <= maximum and maximum >= 1"
            raise ValueError(message)
        if not 1 <= page_size <= 25:
            message = "SearchPicker.page_size must be between 1 and 25"
            raise ValueError(message)
        initial = tuple(picked)
        identities = tuple(identity(item) for item in initial)
        if len(initial) > maximum:
            message = "SearchPicker.picked exceeds maximum"
            raise ValueError(message)
        if len(set(identities)) != len(identities):
            message = "SearchPicker.picked identities must be unique"
            raise ValueError(message)
        self.search = search
        self.identity = identity
        self.label = label
        self.description = description
        self.minimum = minimum
        self.maximum = maximum
        self.picked = initial
        self.on_pick = on_pick
        self.page_size = page_size
        self.copy = copy

    async def _searched(self, event: SubmitEvent) -> None:
        query = str(event.values["query"]).strip()
        source = self.search(query)
        self.query = query
        if self.session is None:
            self.session = BrowseSession(source, identity=self.identity, page_size=self.page_size)
        else:
            self.session.reset(source)

    async def _move(self, movement: Movement, revision: int, _event: ActionEvent) -> None:
        if self.session is not None:
            self.session.move(movement, revision=revision)

    async def _retry(self, revision: int, _event: ActionEvent) -> None:
        if self.session is not None:
            self.session.retry(revision=revision)

    async def _selected(self, revision: int, event: ChoiceEvent) -> None:
        """Add the single visible selection within configured bounds."""
        if len(event.selected) != 1:
            return
        if self.session is None or self.session.revision != revision:
            return
        current = self.session.snapshot
        if current is None:
            return
        item = next(
            (candidate for candidate in current.loaded.window.items if self.identity(candidate) == event.selected[0]),
            None,
        )
        if item is None:
            return
        identities = tuple(self.identity(candidate) for candidate in self.picked)
        item_identity = self.identity(item)
        if item_identity in identities:
            return
        updated = (item,) if self.maximum == 1 else (*self.picked, item)
        if len(updated) > self.maximum:
            return
        self.picked = updated
        await self.on_pick(event, updated)

    async def _remove(self, event: ActionEvent, identity: str) -> None:
        """Remove one picked identity without crossing the minimum."""
        if len(self.picked) <= self.minimum:
            return
        updated = tuple(item for item in self.picked if self.identity(item) != identity)
        if updated == self.picked:
            return
        self.picked = updated
        await self.on_pick(event, updated)

    def _picked_nodes(self) -> tuple[LayoutNode, ...]:
        """Render retained picks with identity-bound removal controls."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nodes: list[LayoutNode] = []
        for item in self.picked:
            identity = self.identity(item)

            async def remove(event: ActionEvent, target: str = identity) -> None:
                await self._remove(event, target)

            nodes.append(
                stack(
                    paragraph(self.label(item)),
                    note(self.description(item)) if self.description is not None else None,
                    action_controls(
                        action_control(
                            chrome.remove,
                            remove,
                            key=f"{self.key}.remove.{identity}",
                            tone=Tone.DANGER,
                            available=len(self.picked) > self.minimum,
                        ),
                        key=f"{self.key}.remove-row.{identity}",
                        display=ControlDisplay.INDIVIDUAL,
                    ),
                )
            )
        return tuple(nodes)

    @override
    def render(self) -> DocumentLike:
        """Render query input, retained picks, and result status."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        search_form = FormSpec(chrome.search, (TextField(key="query", label=chrome.search),))
        search_control = form(
            chrome.search,
            search_form,
            key=f"{self.key}.search",
            on_submit=self._searched,
        )
        if self.session is None:
            return stack(heading(chrome.search), *self._picked_nodes(), search_control)

        current = self.session.snapshot
        operation = self.session.operation
        status = None if operation is None else operation.status
        failed = isinstance(status, Failed)
        message = self.copy.failed if failed else self.copy.loading if isinstance(status, Pending) else None
        if current is None:
            body = self._failure() if failed else (note(self.copy.loading),)
        else:
            body = self._loaded_nodes(current, status_text=message, retry=failed)
        return stack(heading(chrome.search), *self._picked_nodes(), search_control, *body)

    def _failure(self) -> tuple[LayoutNode, ...]:
        """Render a failed search with no stale results."""
        assert self.session is not None
        return (
            note(self.copy.failed),
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.retry-row",
            ),
        )

    def _loaded_nodes(
        self,
        current: BrowseSnapshot[ItemT],
        *,
        status_text: TextLike | None = None,
        retry: bool = False,
    ) -> tuple[ChildLike, ...]:
        """Render one result window with navigation and status."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        assert self.session is not None
        window = current.loaded.window
        entries = tuple(
            choice(
                self.label(item),
                key=self.identity(item),
                description=self.description(item) if self.description is not None else None,
            )
            for item in window.items
        )
        picker = (
            choices(
                *entries,
                key=f"{self.key}.results",
                selection=controlled((), partial(self._selected, self.session.revision)),
                minimum=1,
                maximum=1,
            )
            if entries
            else note(chrome.no_results)
        )
        navigation = (
            action_controls(
                action_control(
                    chrome.previous,
                    partial(self._move, "previous", self.session.revision),
                    key=f"{self.key}.previous",
                    available=self.session.source.capabilities.backward and window.has_previous,
                ),
                action_control(
                    chrome.next,
                    partial(self._move, "next", self.session.revision),
                    key=f"{self.key}.next",
                    available=window.has_next,
                ),
                key=f"{self.key}.navigation",
            )
            if window.has_previous or window.has_next
            else None
        )
        footer = window_footer(chrome, self.session.source, current.loaded, self.page_size)
        return (
            picker,
            navigation,
            note(status_text) if status_text is not None else None,
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.retry-row",
            )
            if retry
            else None,
            note(footer) if footer is not None else None,
        )


__all__ = ["SearchPickHandler", "SearchPicker", "SearchProvider"]
