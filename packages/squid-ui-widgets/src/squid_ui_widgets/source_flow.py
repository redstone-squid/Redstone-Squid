"""Browse a forward-only flow of paragraphs across source batches."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import override

import anyio

from squid_reactivity import state
from squid_ui.authoring.document import DocumentLike
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.factories import action_control, action_controls, note, paragraph, stack
from squid_ui.interactions import ActionEvent
from squid_ui.runtime.definition import Component
from squid_ui.sources import ORIGIN, BatchLoader, BatchSource, Position, SourceStability
from squid_ui_widgets._content import require_key


@dataclass(frozen=True, slots=True)
class _Page:
    number: int | None
    paragraphs: tuple[str, ...]
    remaining: tuple[str, ...]
    next_batch: Position | None
    revision: str | None


class SourceFlow[ItemT](Component):
    """Read a batch source using authored paragraph boundaries.

    max_paragraphs_per_page caps paragraphs per page. fits can impose a tighter budget on the
    paragraph sequence. Each item must contribute at least one paragraph.
    initial_position can open mid-collection with an unknown page number.
    fill_timeout bounds the seconds spent fetching extra batches once content fits.
    Mount a separate flow instance for each independent reading session.
    """

    _page: _Page | None = state(None, opaque=True)

    def __init__(
        self,
        source: BatchSource[ItemT],
        *,
        identity: Callable[[ItemT], str],
        paragraphs: Callable[[ItemT], Sequence[str]],
        max_paragraphs_per_page: int,
        fetch_size: int,
        initial_position: Position = ORIGIN,
        fill_timeout: float = 0.25,
        fits: Callable[[Sequence[str]], bool] | None = None,
        key: str = "flow",
    ) -> None:
        if max_paragraphs_per_page < 1 or source.capabilities.fixed_groups:
            msg = "SourceFlow requires a positive page size and an ungrouped source"
            raise ValueError(msg)
        self.key = require_key(key, name="SourceFlow.key")
        if not 0 <= fill_timeout < float("inf"):
            msg = "fill_timeout must be finite and nonnegative"
            raise ValueError(msg)
        self.fill_timeout = fill_timeout
        self.loader = BatchLoader(source, fetch_size, identity, initial=initial_position)
        self.paragraphs = paragraphs
        self.max_paragraphs_per_page = max_paragraphs_per_page
        self.fits = fits

    async def _read(self, previous: _Page | None, *, start: Position = ORIGIN) -> _Page:
        next_batch = start if previous is None else previous.next_batch
        remaining = () if previous is None else previous.remaining
        revision = None if previous is None else previous.revision
        content: list[str] = []
        index = 0
        number = previous.number + 1 if previous is not None and previous.number is not None else None
        deadline: float | None = None
        while len(content) < self.max_paragraphs_per_page:
            if index == len(remaining):
                if next_batch is None:
                    break
                if content:
                    if deadline is None:
                        deadline = anyio.current_time() + self.fill_timeout
                    if deadline <= anyio.current_time():
                        break
                    with anyio.CancelScope(deadline=deadline) as fill:
                        loaded = await self.loader.load(next_batch)
                    if fill.cancelled_caught:
                        break
                else:
                    loaded = await self.loader.load(next_batch)
                if loaded is None:
                    msg = "source flow read was superseded"
                    raise RuntimeError(msg)
                batch = loaded.batch
                capabilities = self.loader.source.capabilities
                if previous is None and not content and not batch.has_previous:
                    if capabilities.offsets:
                        at_start = batch.position.offset == 0
                    else:
                        at_start = capabilities.backward or batch.position == ORIGIN or start == ORIGIN
                    if at_start:
                        number = 1
                if capabilities.stability is SourceStability.VERIFIED:
                    if revision is not None and batch.revision != revision:
                        msg = "source revision changed while reading the flow"
                        raise ValueError(msg)
                    revision = batch.revision
                if batch.next == next_batch or (not batch.items and batch.has_next):
                    msg = "source batch made no forward progress"
                    raise ValueError(msg)
                fragments: list[str] = []
                for item in batch.items:
                    parts = self.paragraphs(item)
                    if not parts or isinstance(parts, str):
                        msg = "each flow item must supply a sequence of paragraphs"
                        raise ValueError(msg)
                    fragments.extend(parts)
                remaining = tuple(fragments)
                index = 0
                next_batch = batch.next
                if not remaining:
                    break
            if self.fits is not None and not self.fits([*content, remaining[index]]):
                if not content:
                    msg = "an indivisible paragraph exceeds the page budget"
                    raise ValueError(msg)
                break
            content.append(remaining[index])
            index += 1
        return _Page(number, tuple(content), remaining[index:], next_batch, revision)

    @override
    async def on_load(self) -> None:
        self._page = await self._read(None, start=self.loader.initial)

    async def _first(self, delivered: _Page, _event: ActionEvent) -> None:
        if self._page is delivered:
            self._page = await self._read(None)

    async def _advance(self, delivered: _Page, _event: ActionEvent) -> None:
        # A failed delivery leaves the old binding live. Reuse its prepared successor.
        if self._page is delivered:
            candidate = await self._read(delivered)
            self._page = candidate if candidate.paragraphs else replace(delivered, next_batch=None)

    @override
    def render(self) -> DocumentLike:
        page = self._page
        if page is None or not page.paragraphs:
            return note("No entries")
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        controls = []
        if page.number is None:
            controls.append(action_control("First", partial(self._first, page), key=f"{self.key}.first"))
        if page.remaining or page.next_batch is not None:
            controls.append(action_control(chrome.next, partial(self._advance, page), key=f"{self.key}.next"))
        return stack(
            *(paragraph(part) for part in page.paragraphs),
            action_controls(*controls, key=f"{self.key}.navigation") if controls else None,
            note(f"Page {page.number}") if page.number is not None else None,
        )
