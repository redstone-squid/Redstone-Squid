"""Factories for exact Discord Components V2 layouts."""

from typing import Literal, cast

from squid_ui.assets import Asset
from squid_ui.primitives.nodes import (
    Button,
    File,
    Gallery,
    GalleryItem,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
    PremiumButton,
    RawItem,
    RoutedButton,
    Section,
    Sep,
    Text,
    Thumbnail,
)
from squid_ui.primitives.styles import Color
from squid_ui.target_types import ComponentsV2Target, Renderable
from squid_ui.text import TextLike
from squid_ui_discord._exact_factories import (
    button,
    code,
    controls,
    entity_select,
    footer,
    heading,
    lines,
    link_button,
    option,
    premium_button,
    routed_button,
    routed_select,
    row,
    select,
    text,
)

type Child = Renderable[ComponentsV2Target] | TextLike | None | Literal[False]
type SectionText = Text | TextLike | None | Literal[False]


def file(asset: Asset, *, spoiler: bool = False) -> File:
    """Build a visible file component backed by ``asset``."""
    return File(asset.key, asset.name, asset.media_type, spoiler)


def separator(*, large: bool = False, visible: bool = True) -> Sep:
    """Build an exact Components V2 separator."""
    return Sep(large, visible)


def thumbnail(url: str, *, description: TextLike | None = None, spoiler: bool = False) -> Thumbnail:
    """Build an exact section thumbnail."""
    return Thumbnail(url, description, spoiler)


def gallery_item(url: str, *, description: TextLike | None = None, spoiler: bool = False) -> GalleryItem:
    """Build one exact gallery item."""
    return GalleryItem(url, description, spoiler)


def gallery(*items: str | GalleryItem) -> Gallery:
    """Build one exact target gallery."""
    return Gallery(tuple(items))


def media(*items: str | GalleryItem) -> MediaCollection:
    """Build media that may be arranged across valid galleries."""
    return MediaCollection(tuple(items))


def section(
    *texts: SectionText,
    accessory: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem,
) -> Section:
    """Build exact text beside a Discord section accessory."""
    normalized = tuple(
        _text_child(value, "section", index)
        for index, value in enumerate(texts)
        if value is not None and value is not False
    )
    return Section(normalized, accessory)


def panel(*children: Child, accent: Color | None = None, spoiler: bool = False) -> Panel:
    """Build an exact Components V2 container with normalized children."""
    normalized: list[Node] = []
    for index, child in enumerate(children):
        if child is None or child is False:
            continue
        if child is True:
            message = f"panel argument {index}: True is not content"
            raise TypeError(message)
        if isinstance(child, Renderable):
            normalized.append(cast(Node, child))
        else:
            normalized.append(Text(child))
    return Panel(tuple(normalized), accent, spoiler)


def _text_child(value: SectionText, origin: str, index: int) -> Text:
    if value is None or value is False:
        message = f"{origin} argument {index}: omitted content reached normalization"
        raise TypeError(message)
    if value is True:
        message = f"{origin} argument {index}: True is not content"
        raise TypeError(message)
    return value if isinstance(value, Text) else Text(value)


__all__ = [
    "button",
    "code",
    "controls",
    "entity_select",
    "file",
    "footer",
    "gallery",
    "gallery_item",
    "heading",
    "lines",
    "link_button",
    "media",
    "option",
    "panel",
    "premium_button",
    "routed_button",
    "routed_select",
    "row",
    "section",
    "select",
    "separator",
    "text",
    "thumbnail",
]
