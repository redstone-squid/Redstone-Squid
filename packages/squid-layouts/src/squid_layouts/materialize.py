"""Turn a solved layout into discord.py Components V2 objects.

This is the mechanical half of the pipeline: no budget arithmetic, no gate. Callers go
through :func:`~squid_layouts.compositor.compose`, which solves and conforms around it.
Interactive nodes (Button, SelectMenu) require a wiring callback, which `Mount` provides.
"""

import itertools
from collections.abc import Callable

import discord

from squid_layouts.ir import Button, Gallery, LinkButton, RawItem, Row, SelectMenu, Sep, Thumbnail
from squid_layouts.solve import Realized, RPanel, RSection, RText, SolvedLayout

type Wire = Callable[[Button | SelectMenu, str], discord.ui.Item]


class StaticView(discord.ui.LayoutView):
    """A rendered view with no interaction handlers and no timeout."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


def _reject_interactive(node: Button | SelectMenu, key: str) -> discord.ui.Item:
    message = "interactive nodes need a Mount; render_static only handles static documents"
    raise TypeError(message)


def materialize(
    solved: SolvedLayout,
    *,
    into: discord.ui.LayoutView | None = None,
    wire: Wire = _reject_interactive,
) -> discord.ui.LayoutView:
    """Build a `LayoutView` from a solved layout, without re-checking budgets.

    Args:
        solved: The solver's output.
        into: The view to fill; a fresh :class:`StaticView` when omitted.
        wire: Builds the discord.py item for an interactive node given its dispatch key.
            Keys come from the node's explicit ``key`` or its materialization position.
    """
    view = into if into is not None else StaticView()
    positions = itertools.count()

    def accessory_item(accessory: Thumbnail | LinkButton | Button | RawItem) -> discord.ui.Item:
        match accessory:
            case Thumbnail(url=url, description=description):
                return discord.ui.Thumbnail(url, description=description)
            case LinkButton(label=label, url=url):
                return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)
            case Button():
                return wire(accessory, accessory.key or f"auto{next(positions)}")
            case RawItem(factory=factory):
                return factory()

    def item(child: Realized) -> discord.ui.Item:
        match child:
            case RText(content=content):
                return discord.ui.TextDisplay(content)
            case RPanel(children=children, accent=accent):
                return discord.ui.Container(*(item(inner) for inner in children), accent_colour=accent)
            case RSection(texts=texts, accessory=accessory):
                return discord.ui.Section(
                    *(discord.ui.TextDisplay(slot.content) for slot in texts),
                    accessory=accessory_item(accessory),
                )
            case Sep(large=large, visible=visible):
                spacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
                return discord.ui.Separator(visible=visible, spacing=spacing)
            case Row(items=items):
                return discord.ui.ActionRow(*(accessory_item(entry) for entry in items))
            case SelectMenu():
                return discord.ui.ActionRow(wire(child, child.key or f"auto{next(positions)}"))
            case Gallery(urls=urls):
                return discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in urls))
            case Thumbnail() | LinkButton() | RawItem():
                return accessory_item(child)

    for child in solved.children:
        view.add_item(item(child))
    return view
