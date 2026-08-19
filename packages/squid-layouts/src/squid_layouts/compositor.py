"""The compositor: the one pipeline from IR to a delivered-shaped view.

Every path that turns nodes into a `LayoutView` — the sessionless `render_static`, `Mount`'s
per-generation build, hosts embedding one card inside a larger post — goes through
:func:`compose`, so solving, materialization, the conform gate, and degradation logging happen
in the same order every time. Callers that assemble views by hand skip the gate; that is the
bug this module exists to make unnecessary.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import discord

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.conform import conform
from squid_layouts.ir import Node, as_nodes
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.materialize import Wire, materialize
from squid_layouts.solve import SolvedLayout, solve

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Composition:
    """A built view plus what the engine had to give up to build it."""

    view: discord.ui.LayoutView
    solved: SolvedLayout
    interventions: list[str]
    """Conform's clamps; solver degradations stay on ``solved.notes``."""

    @property
    def page(self) -> int:
        """The page shown, 0 for an unpaginated document."""
        return self.solved.page

    @property
    def pages(self) -> int:
        return self.solved.pages


def compose(
    rendered: Node | Sequence[Node],
    *,
    into: discord.ui.LayoutView | None = None,
    wire: Wire | None = None,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reserved_text: int = 0,
    page: int | None = None,
    nav: Callable[[int, int], Sequence[Node]] | None = None,
) -> Composition:
    """Solve, materialize, and conform ``rendered`` in one pass.

    Args:
        rendered: A node or sequence of nodes, as a component's ``render`` returns them.
        into: The view to fill; a fresh :class:`~squid_layouts.materialize.StaticView` when omitted.
        wire: Builds the discord.py item for an interactive node; ``None`` rejects them,
            which is what a static document wants.
        limits: The limit table supplying the text and component budgets.
        chrome: Pre-translated framework strings.
        strict: Raise instead of degrading.
        reserved_text: Characters held back from the display budget for content this call
            does not see — what a caller composing one card into a larger message must pass.
        page: The page to show when the document paginates; clamped, and ``None`` adopts the
            pager's initial page.
        nav: Builds the page controls from ``(page, pages)``; only called when the document
            paginates. Static compositions leave it out and show no controls.
    """
    solved = solve(
        as_nodes(rendered),
        limits=limits,
        chrome=chrome,
        strict=strict,
        reserved_text=reserved_text,
        page=page,
        nav=nav,
    )
    view = materialize(solved, into=into) if wire is None else materialize(solved, into=into, wire=wire)
    interventions = conform(view, strict=strict, limits=limits)
    if solved.notes or interventions:
        logger.warning("layout degraded: %s", "; ".join((*solved.notes, *interventions)))
    return Composition(view=view, solved=solved, interventions=interventions)


def render_static(
    nodes: Sequence[Node] | Node,
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reserved_text: int = 0,
) -> discord.ui.LayoutView:
    """Compose a static document — no handlers, no session — and return just the view."""
    return compose(nodes, limits=limits, chrome=chrome, strict=strict, reserved_text=reserved_text).view
