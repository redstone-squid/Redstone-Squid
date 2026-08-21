"""Page navigation as IR the solver can see.

Nav controls used to be an ActionRow bolted onto the finished view, charged to the budget as
a constant. They are ordinary nodes now: a factory turns the page state into components, the
solver realizes and counts them, and a view that wants page jumps or a "Newest" button
supplies its own factory instead of patching the mount.

Factories must return component-bearing nodes only — buttons and selects cost no display
text, which is what lets the solver add them after it has allocated the text budget. They
must also return the same *shape* on every page: disable a control at the ends rather than
hiding it, so the component count a page turn produces is the one the solver budgeted for.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from squid_layouts.chrome import Chrome
from squid_layouts.primitives.nodes import Button, Gallery, RawItem, Row, SelectMenu, Sep, Thumbnail

PREV_KEY = "__page_prev"
NEXT_KEY = "__page_next"


@dataclass(frozen=True, slots=True)
class PageContext:
    """Where the reader is, and how to move them."""

    key: str
    page: int
    """0-based and already clamped."""
    pages: int
    on_prev: Callable[..., Awaitable[None]]
    on_next: Callable[..., Awaitable[None]]

    @property
    def at_start(self) -> bool:
        return self.page <= 0

    @property
    def at_end(self) -> bool:
        return self.page >= self.pages - 1


type NavNode = Row | SelectMenu | Sep | Thumbnail | Gallery | RawItem
"""What a nav factory may return: components, never display text."""

type NavFactory = Callable[[PageContext], Sequence[NavNode]]
"""The authoring form, closed over the handlers that move the page."""

type PageNav = Callable[[str, int, int], Sequence[NavNode]]
"""The planning form. Planning has no handlers to offer, so a mount adapts its
`NavFactory` down to this and keeps the callbacks on its own side."""


def page_controls(chrome: Chrome, context: PageContext) -> Row:
    """The Previous/Next row, disabled at the ends rather than hidden."""
    return Row(
        (
            Button(
                label=chrome.previous,
                on_click=context.on_prev,
                key=f"{PREV_KEY}.{context.key}",
                disabled=context.at_start,
            ),
            Button(
                label=chrome.next, on_click=context.on_next, key=f"{NEXT_KEY}.{context.key}", disabled=context.at_end
            ),
        )
    )


def default_nav(chrome: Chrome) -> NavFactory:
    """The stock factory: one Previous/Next row, labelled from `chrome`."""

    def factory(context: PageContext) -> Sequence[NavNode]:
        return (page_controls(chrome, context),)

    return factory
