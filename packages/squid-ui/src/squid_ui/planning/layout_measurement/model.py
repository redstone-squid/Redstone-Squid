"""Realized node values produced by concrete layout measurement."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from squid_ui.planning.limits import Axis
from squid_ui.primitives.nodes import (
    Button,
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
from squid_ui.primitives.styles import Color
from squid_ui.temporal import ZonedDateTime


@dataclass(slots=True)
class MeasuredText:
    content: str = ""
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class MeasuredTime:
    instant: datetime
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class MeasuredZonedTime:
    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class MeasuredSection:
    texts: list[MeasuredText]
    accessory: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class MeasuredPanel:
    children: list[Realized]
    accent: Color | None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class MeasuredGroup:
    """A transparent realized group removed before scene conversion."""

    children: list[Realized]


@dataclass(frozen=True, slots=True)
class MeasuredContent:
    """The realized `content` field, whose text was allocated from its own pool."""

    slot: MeasuredText


@dataclass(frozen=True, slots=True)
class MeasuredCardField:
    name: MeasuredText
    value: MeasuredText
    inline: bool


@dataclass(frozen=True, slots=True)
class MeasuredCard:
    """One realized embed. Every slot already holds its final, allocated string."""

    title: MeasuredText | None
    url: str | None
    blocks: list[Realized]
    """Description blocks, joined by the dialect once their text is allocated."""
    fields: list[MeasuredCardField]
    footer: MeasuredText | None
    footer_icon: str | None
    author: MeasuredText | None
    author_url: str | None
    author_icon: str | None
    accent: Color | None
    image: CardMedia | None
    thumbnail: CardMedia | None
    timestamp: ZonedDateTime | datetime | None


type Realized = (
    MeasuredText
    | MeasuredTime
    | MeasuredZonedTime
    | MeasuredSection
    | MeasuredPanel
    | MeasuredGroup
    | MeasuredCard
    | MeasuredContent
    | File
    | Sep
    | Row
    | Button
    | LinkButton
    | PremiumButton
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
    slot: MeasuredText
    prefix: str
    suffix: str
    fragments: list[str]
    footer_slot: MeasuredText
    footer: Callable[[int, int], str]
    axis: Axis = Axis.DISPLAY_TEXT
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
