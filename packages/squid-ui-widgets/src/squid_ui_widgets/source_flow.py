"""Browse a forward-only flow of paragraphs across source windows."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import override

from squid_reactivity import state
from squid_ui.authoring.document import DocumentLike
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.factories import action_control, action_controls, note, paragraph, stack
from squid_ui.interactions import ActionEvent
from squid_ui.runtime.definition import Component
from squid_ui.sources import ORIGIN, Position, SourceStability, WindowLoader, WindowSource
from squid_ui_widgets._content import require_key


@dataclass(frozen=True, slots=True)
class _Page:
    number: int
    paragraphs: tuple[str, ...]
    remaining: tuple[str, ...]
    next_window: Position | None
    revision: str | None


class SourceFlow[ItemT](Component):
    """Read a window source using authored paragraph boundaries.

    page_size caps paragraphs per page. fits can impose a tighter budget on the
    paragraph sequence. Each item must contribute at least one paragraph.
    Mount a separate flow instance for each independent reading session.
    """

    _page: _Page | None = state(None, opaque=True)

    def __init__(
        self,
        source: WindowSource[ItemT],
        *,
        identity: Callable[[ItemT], str],
        paragraphs: Callable[[ItemT], Sequence[str]],
        page_size: int,
        fetch_size: int,
        fits: Callable[[Sequence[str]], bool] | None = None,
        key: str = "flow",
    ) -> None:
        if page_size < 1 or source.capabilities.fixed_groups:
            msg = "SourceFlow requires a positive page size and an ungrouped source"
            raise ValueError(msg)
        self.key = require_key(key, name="SourceFlow.key")
        self.loader = WindowLoader(source, fetch_size, identity)
        self.paragraphs = paragraphs
        self.page_size = page_size
        self.fits = fits

    async def _read(self, previous: _Page | None) -> _Page:
        next_window = ORIGIN if previous is None else previous.next_window
        remaining = () if previous is None else previous.remaining
        revision = None if previous is None else previous.revision
        content: list[str] = []
        index = 0
        while len(content) < self.page_size:
            if index == len(remaining):
                if next_window is None:
                    break
                loaded = await self.loader.load(next_window)
                if loaded is None:
                    msg = "source flow read was superseded"
                    raise RuntimeError(msg)
                window = loaded.window
                if self.loader.source.capabilities.stability is SourceStability.VERIFIED:
                    if revision is not None and window.revision != revision:
                        msg = "source revision changed while reading the flow"
                        raise ValueError(msg)
                    revision = window.revision
                if window.next == next_window or (not window.items and window.has_next):
                    msg = "source window made no forward progress"
                    raise ValueError(msg)
                fragments: list[str] = []
                for item in window.items:
                    parts = self.paragraphs(item)
                    if not parts or isinstance(parts, str):
                        msg = "each flow item must supply a sequence of paragraphs"
                        raise ValueError(msg)
                    fragments.extend(parts)
                remaining = tuple(fragments)
                index = 0
                next_window = window.next
                if not remaining:
                    break
            if self.fits is not None and not self.fits([*content, remaining[index]]):
                if not content:
                    msg = "an indivisible paragraph exceeds the page budget"
                    raise ValueError(msg)
                break
            content.append(remaining[index])
            index += 1
        return _Page(
            1 if previous is None else previous.number + 1, tuple(content), remaining[index:], next_window, revision
        )

    @override
    async def on_load(self) -> None:
        self._page = await self._read(None)

    async def _advance(self, delivered: _Page, _event: ActionEvent) -> None:
        # A failed delivery leaves the old binding live. Reuse its prepared successor.
        if self._page is delivered:
            candidate = await self._read(delivered)
            self._page = candidate if candidate.paragraphs else replace(delivered, next_window=None)

    @override
    def render(self) -> DocumentLike:
        page = self._page
        if page is None or not page.paragraphs:
            return note("No entries")
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        return stack(
            *(paragraph(part) for part in page.paragraphs),
            action_controls(
                action_control(chrome.next, partial(self._advance, page), key=f"{self.key}.next"),
                key=f"{self.key}.navigation",
            )
            if page.remaining or page.next_window is not None
            else None,
            note(f"Page {page.number}"),
        )
