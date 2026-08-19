"""Turn a solved layout into discord.py Components V2 objects.

`render_static` is the sessionless path: it solves, materializes, and runs the conform gate,
so callers get a `LayoutView` that is guaranteed to fit — suitable for reconciler-managed
posts, sticky messages, and any send without interaction handlers.
"""

import logging
from collections.abc import Sequence

import discord

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.conform import conform
from squid_layouts.ir import Gallery, LinkButton, Node, RawItem, Row, Sep, Thumbnail
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.solve import Realized, RPanel, RSection, RText, SolvedLayout, solve

logger = logging.getLogger(__name__)


class StaticView(discord.ui.LayoutView):
    """A rendered view with no interaction handlers and no timeout."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


def _accessory_item(accessory: Thumbnail | LinkButton | RawItem) -> discord.ui.Item:
    match accessory:
        case Thumbnail(url=url, description=description):
            return discord.ui.Thumbnail(url, description=description)
        case LinkButton(label=label, url=url):
            return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)
        case RawItem(factory=factory):
            return factory()


def _item(child: Realized) -> discord.ui.Item:
    match child:
        case RText(content=content):
            return discord.ui.TextDisplay(content)
        case RPanel(children=children, accent=accent):
            return discord.ui.Container(*(_item(inner) for inner in children), accent_colour=accent)
        case RSection(texts=texts, accessory=accessory):
            return discord.ui.Section(
                *(discord.ui.TextDisplay(slot.content) for slot in texts),
                accessory=_accessory_item(accessory),
            )
        case Sep(large=large, visible=visible):
            spacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
            return discord.ui.Separator(visible=visible, spacing=spacing)
        case Row(items=items):
            return discord.ui.ActionRow(*(_accessory_item(item) for item in items))
        case Gallery(urls=urls):
            return discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in urls))
        case Thumbnail() | LinkButton() | RawItem():
            return _accessory_item(child)


def materialize(solved: SolvedLayout) -> discord.ui.LayoutView:
    """Build a `LayoutView` from a solved layout, without re-checking budgets."""
    view = StaticView()
    for child in solved.children:
        view.add_item(_item(child))
    return view


def render_static(
    nodes: Sequence[Node] | Node,
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
) -> discord.ui.LayoutView:
    """Solve, materialize, and conform a document in one call.

    In strict mode any degradation raises; otherwise degradations are logged once and the
    view is delivered clamped.
    """
    node_list = [nodes] if not isinstance(nodes, Sequence) else list(nodes)
    solved = solve(node_list, limits=limits, chrome=chrome, strict=strict)
    view = materialize(solved)
    interventions = conform(view, strict=strict, limits=limits)
    if solved.notes or interventions:
        logger.warning("layout degraded: %s", "; ".join((*solved.notes, *interventions)))
    return view
