"""Search-then-pick interaction for non-platform domain entities."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from squid_layouts.actions import ActionEvent, SubmitEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.factories import action, actions, choice, choices, controlled, form, heading, note, paragraph, stack
from squid_layouts.forms import FormSpec, TextField
from squid_layouts.patterns._content import require_key
from squid_layouts.runtime.component import Component, RenderResult
from squid_layouts.runtime.reactivity import state
from squid_layouts.runtime.resources import Failed, Pending, Ready, resource
from squid_layouts.semantic import ActionDisplay, ChoiceEvent, LayoutNode, Tone
from squid_layouts.sources import LoadedWindow, WindowLoader, WindowSource, window_footer
from squid_layouts.text import TextLike

type LookupSearch[ItemT] = Callable[[str], WindowSource[ItemT]]
type LookupPickHandler[ItemT] = Callable[[ActionEvent, tuple[ItemT, ...]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _LookupRequest:
    operation: Literal["refresh", "previous", "next"] = "refresh"
    generation: int = 0


@dataclass(frozen=True, slots=True)
class _LookupWindow[ItemT]:
    query: str
    source: WindowSource[ItemT]
    loader: WindowLoader[ItemT]
    loaded: LoadedWindow[ItemT]


class Lookup[ItemT](Component):
    """Search a windowed domain source and retain the resolved items a reader picks."""

    query: str | None = state(None)
    picked: tuple[ItemT, ...] = state((), persist=False, opaque=True)
    _request: _LookupRequest = state(default=_LookupRequest(), persist=False)

    def __init__(
        self,
        search: LookupSearch[ItemT],
        *,
        key: str = "lookup",
        identity: Callable[[ItemT], str],
        label: Callable[[ItemT], TextLike],
        description: Callable[[ItemT], TextLike] | None = None,
        minimum: int = 0,
        maximum: int = 1,
        picked: Sequence[ItemT] = (),
        on_pick: LookupPickHandler[ItemT],
        page_size: int = 10,
        loading: TextLike = "Loading…",
        load_failed: TextLike = "Could not load results.",
        retry: TextLike = "Retry",
    ) -> None:
        self.key = require_key(key, name="Lookup.key")
        if minimum < 0 or maximum < 1 or minimum > maximum:
            message = "Lookup bounds must satisfy 0 <= minimum <= maximum and maximum >= 1"
            raise ValueError(message)
        if not 1 <= page_size <= 25:
            message = "Lookup.page_size must be between 1 and 25"
            raise ValueError(message)
        initial = tuple(picked)
        identities = tuple(identity(item) for item in initial)
        if len(initial) > maximum:
            message = "Lookup.picked exceeds maximum"
            raise ValueError(message)
        if len(set(identities)) != len(identities):
            message = "Lookup.picked identities must be unique"
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
        self.loading = loading
        self.load_failed = load_failed
        self.retry = retry

    @resource
    async def results(self) -> _LookupWindow[ItemT]:
        query = self.query
        if query is None:
            message = "Lookup.results was observed before a query was submitted"
            raise LayoutInvariantError(message)
        # Read before the branch, not inside it: the request selects the operation on every
        # run, so a run that happens not to consult it still depends on it.
        request = self._request
        current = self.results.state
        previous = current.previous.value if isinstance(current, Pending | Failed) and current.previous else None
        if previous is None or previous.query != query:
            source = self.search(query)
            loader = WindowLoader(source, self.page_size, self.identity)
            loaded = await loader.load()
        else:
            source = previous.source
            loader = previous.loader
            match request.operation:
                case "previous":
                    loaded = await loader.previous(previous.loaded)
                case "next":
                    loaded = await loader.next(previous.loaded)
                case _:
                    loaded = await loader.load(previous=previous.loaded)
        if loaded is None:
            message = "lookup window request was superseded before it loaded"
            raise LayoutInvariantError(message)
        return _LookupWindow(query, source, loader, loaded)

    async def _searched(self, event: SubmitEvent) -> None:
        query = str(event.values["query"]).strip()
        self.query = query
        self._request = _LookupRequest(generation=self._request.generation + 1)

    async def _previous(self, _event: ActionEvent) -> None:
        self._request = _LookupRequest("previous", self._request.generation + 1)

    async def _next(self, _event: ActionEvent) -> None:
        self._request = _LookupRequest("next", self._request.generation + 1)

    async def _retry(self, _event: ActionEvent) -> None:
        self._request = _LookupRequest(self._request.operation, self._request.generation + 1)

    def _visible(self) -> _LookupWindow[ItemT] | None:
        current = self.results.state
        if isinstance(current, Ready):
            return current.value
        if isinstance(current, Pending | Failed) and current.previous is not None:
            return current.previous.value
        return None

    async def _selected(self, event: ChoiceEvent) -> None:
        if len(event.selected) != 1:
            return
        current = self._visible()
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
        if len(self.picked) <= self.minimum:
            return
        updated = tuple(item for item in self.picked if self.identity(item) != identity)
        if updated == self.picked:
            return
        self.picked = updated
        await self.on_pick(event, updated)

    def _picked_nodes(self) -> tuple[LayoutNode, ...]:
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
                    actions(
                        action(
                            chrome.remove,
                            remove,
                            key=f"{self.key}.remove.{identity}",
                            tone=Tone.DANGER,
                            available=len(self.picked) > self.minimum,
                        ),
                        key=f"{self.key}.remove-row.{identity}",
                        display=ActionDisplay.INDIVIDUAL,
                    ),
                )
            )
        return tuple(nodes)

    def render(self) -> RenderResult:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        search_form = FormSpec(chrome.search, (TextField(key="query", label=chrome.search),))
        search_control = form(
            chrome.search,
            search_form,
            key=f"{self.key}.search",
            on_submit=self._searched,
        )
        if self.query is None:
            return stack(heading(chrome.search), *self._picked_nodes(), search_control)

        match self.results.state:
            case Pending(previous=None):
                body = (note(self.loading),)
            case Failed(previous=None):
                body = self._failure()
            case Pending(previous=Ready(value=current)):
                body = self._loaded_nodes(current, status_text=self.loading)
            case Failed(previous=Ready(value=current)):
                body = self._loaded_nodes(current, status_text=self.load_failed, retry=True)
            case Ready(value=current):
                body = self._loaded_nodes(current)
        return stack(heading(chrome.search), *self._picked_nodes(), search_control, *body)

    def _failure(self) -> tuple[LayoutNode, ...]:
        return (
            note(self.load_failed),
            actions(action(self.retry, self._retry, key=f"{self.key}.retry"), key=f"{self.key}.retry-row"),
        )

    def _loaded_nodes(
        self,
        current: _LookupWindow[ItemT],
        *,
        status_text: TextLike | None = None,
        retry: bool = False,
    ) -> tuple[LayoutNode, ...]:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
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
                selection=controlled((), self._selected),
                minimum=1,
                maximum=1,
            )
            if entries
            else note(chrome.no_results)
        )
        navigation = (
            actions(
                action(
                    chrome.previous,
                    self._previous,
                    key=f"{self.key}.previous",
                    available=current.source.capabilities.backward and window.has_previous,
                ),
                action(chrome.next, self._next, key=f"{self.key}.next", available=window.has_next),
                key=f"{self.key}.navigation",
            )
            if window.has_previous or window.has_next
            else None
        )
        footer = window_footer(chrome, current.source, current.loaded, self.page_size)
        return (
            picker,
            navigation,
            note(status_text) if status_text is not None else None,
            actions(action(self.retry, self._retry, key=f"{self.key}.retry"), key=f"{self.key}.retry-row")
            if retry
            else None,
            note(footer) if footer is not None else None,
        )


__all__ = ["Lookup", "LookupPickHandler", "LookupSearch"]
