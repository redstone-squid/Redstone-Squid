"""Factories for exact Discord Components V2 layouts."""

from typing import Literal, cast

from squid_ui.assets import Asset
from squid_ui.primitives.nodes import (
    Button,
    File,
    Footer,
    Gallery,
    GalleryItem,
    Heading,
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
from squid_ui_discord._factories import (
    button,
    check_content,
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

type Child[ContextT = object] = Renderable[ComponentsV2Target, ContextT] | TextLike | Literal[False] | None
type SectionText = Text | Heading | Footer | TextLike | Literal[False] | None


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


def section[ContextT = object](
    *texts: SectionText,
    accessory: Thumbnail | LinkButton | PremiumButton | Button[ContextT] | RoutedButton | RawItem,
) -> Section[ContextT]:
    """Build exact text beside a Discord section accessory."""
    normalized = tuple(
        _text_child(value, "section", index)
        for index, value in enumerate(texts)
        if value is not None and value is not False
    )
    return Section(normalized, accessory)


def panel[ContextT = object](
    *children: Child[ContextT], accent: Color | None = None, spoiler: bool = False
) -> Panel[ContextT]:
    """Build an exact Components V2 container with normalized children."""
    normalized: list[Node[ContextT]] = []
    for index, child in enumerate(children):
        if child is None or child is False:
            continue
        check_content(child, "panel", index)
        if isinstance(child, Renderable):
            normalized.append(cast(Node[ContextT], child))
        else:
            normalized.append(Text(child))
    return Panel(tuple(normalized), accent, spoiler)


def _text_child(value: SectionText, origin: str, index: int) -> Text | Heading | Footer:
    if value is None or value is False:
        message = f"{origin} argument {index}: omitted content reached normalization"
        raise TypeError(message)
    check_content(value, origin, index)
    return value if isinstance(value, Text | Heading | Footer) else Text(value)


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
