"""Realized node values produced by concrete layout measurement."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from squid_layouts.planning.limits import DISPLAY_TEXT
from squid_layouts.primitives.nodes import (
    CardMedia,
    EntitySelect,
    File,
    Gallery,
    LinkButton,
    PremiumButton,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Thumbnail,
)
from squid_layouts.primitives.styles import Color
from squid_layouts.temporal import ZonedDateTime


@dataclass(slots=True)
class RText:
    content: str = ""
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class RTime:
    instant: datetime
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class RZonedTime:
    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class RSection:
    texts: list[RText]
    accessory: Thumbnail | LinkButton | PremiumButton | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class RPanel:
    children: list[Realized]
    accent: Color | None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class RGroup:
    """A transparent realized group removed before scene conversion."""

    children: list[Realized]


@dataclass(frozen=True, slots=True)
class RContent:
    """The realized `content` field, whose text was allocated from its own pool."""

    slot: RText


@dataclass(frozen=True, slots=True)
class RCardField:
    name: RText
    value: RText
    inline: bool


@dataclass(frozen=True, slots=True)
class RCard:
    """One realized embed. Every slot already holds its final, allocated string."""

    title: RText | None
    url: str | None
    blocks: list[Realized]
    """Description blocks, joined by the dialect once their text is allocated."""
    fields: list[RCardField]
    footer: RText | None
    footer_icon: str | None
    author: RText | None
    author_url: str | None
    author_icon: str | None
    accent: Color | None
    image: CardMedia | None
    thumbnail: CardMedia | None
    timestamp: ZonedDateTime | datetime | None


type Realized = (
    RText
    | RTime
    | RZonedTime
    | RSection
    | RPanel
    | RGroup
    | RCard
    | RContent
    | File
    | Sep
    | Row
    | SelectMenu
    | EntitySelect
    | RoutedSelect
    | Thumbnail
    | Gallery
    | RawItem
)


PAGE_FOOTER_PREFIX = "-# "


@dataclass(slots=True)
class Pager:
    """Page state for one keyed Paginate node that overflowed."""

    key: str
    slot: RText
    prefix: str
    suffix: str
    fragments: list[str]
    footer_slot: RText
    footer: Callable[[int, int], str]
    axis: str = DISPLAY_TEXT
    """The text pool this pager's body and footer draw from."""
    initial: int = 0
    """The page to open on; a mount adopts this before its first render."""
    page: int = 0
    nav_host: list[Realized] | None = None
    """The realized list holding this pager's nav, so `repage` can replace it in place."""
    nav_at: int = 0
    nav_count: int = 0

    @property
    def pages(self) -> int:
        return len(self.fragments)

    def select(self, index: int) -> int:
        """Render page ``index`` (clamped) into the document; returns the page shown."""
        index = max(0, min(index, self.pages - 1))
        self.slot.content = self.prefix + self.fragments[index] + self.suffix
        self.footer_slot.content = PAGE_FOOTER_PREFIX + self.footer(index + 1, self.pages)
        self.page = index
        return index
