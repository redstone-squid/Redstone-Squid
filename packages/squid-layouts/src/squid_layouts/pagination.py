"""Page navigation as IR the solver can see.

Nav controls used to be an ActionRow bolted onto the finished view, charged to the budget as
a constant. They are ordinary nodes now: a factory turns the page state into components, the
solver realizes and counts them, and a view that wants page jumps or a "Newest" button
supplies its own factory instead of patching the mount.

Factories must return component-bearing nodes only — buttons and selects cost no display
text, which is what lets the solver add them after it has allocated the text budget.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from squid_layouts.chrome import Chrome
from squid_layouts.ir import Button, Node, Row

PREV_KEY = "__page_prev"
NEXT_KEY = "__page_next"


@dataclass(frozen=True, slots=True)
class PageContext:
    """Where the reader is, and how to move them."""

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


type NavFactory = Callable[[PageContext], Sequence[Node]]


def page_controls(chrome: Chrome, context: PageContext) -> Row:
    """The Previous/Next row, disabled at the ends rather than hidden."""
    return Row(
        (
            Button(label=chrome.previous, on_click=context.on_prev, key=PREV_KEY, disabled=context.at_start),
            Button(label=chrome.next, on_click=context.on_next, key=NEXT_KEY, disabled=context.at_end),
        )
    )


def default_nav(chrome: Chrome) -> NavFactory:
    """The stock factory: one Previous/Next row, labelled from `chrome`."""

    def factory(context: PageContext) -> Sequence[Node]:
        return (page_controls(chrome, context),)

    return factory
