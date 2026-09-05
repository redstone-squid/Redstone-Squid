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
    """The slot a `TextUnit` writes its allocated string into; a `Pager` rewrites it per page.

    `dropped` marks a node the allocator removed or one that resolved to empty text; `prune`
    omits it from the layout.
    """

    content: str = ""
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class MeasuredTime:
    """A `Time` node, charged to the text pool at the length of its `<t:unix:style>` token."""

    instant: datetime
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class MeasuredZonedTime:
    """A `ZonedTime` node, charged to the text pool at the length of its ISO 8601 form."""

    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class MeasuredSection:
    """A Components V2 section: at most three texts beside one accessory."""

    texts: list[MeasuredText]
    accessory: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class MeasuredPanel:
    """A Components V2 container; `prune` removes it once allocation empties it."""

    children: list[Realized]
    accent: Color | None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class MeasuredGroup:
    """A realized `Budget` or `Break`; `prune` splices its children into the parent."""

    children: list[Realized]


@dataclass(frozen=True, slots=True)
class MeasuredContent:
    """The message `content` field; its text draws from `Axis.CONTENT_TEXT`, not the display pool."""

    slot: MeasuredText


@dataclass(frozen=True, slots=True)
class MeasuredCardField:
    """One embed field; `name` and `value` are clamped to `embeds.field_name` and `embeds.field_value`."""

    name: MeasuredText
    value: MeasuredText
    inline: bool


@dataclass(frozen=True, slots=True)
class MeasuredCard:
    """One embed; each text slot is clamped to its own embed limit before it joins the pool."""

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
    """Page state for one keyed `Paginate` node that split into more than one fragment.

    Every fragment already fits the grant the node was allocated, so `select` never re-fits.
    """

    key: str
    slot: MeasuredText
    prefix: str
    suffix: str
    """Chrome wrapped around every fragment: a heading marker, a footer dash, code fences."""
    fragments: list[str]
    footer_slot: MeasuredText
    footer: Callable[[int, int], str]
    axis: Axis = Axis.DISPLAY_TEXT
    """The text pool this pager's body and footer draw from."""
    initial: int = 0
    """The page to open on; a mount adopts this before its first render."""
    page: int = 0
    nav_host: list[Realized] | None = None
    """The realized list holding this pager's nav, so `MeasuredLayout.reposition` can replace it in place."""
    nav_at: int = 0
    nav_count: int = 0

    @property
    def pages(self) -> int:
        return len(self.fragments)

    def select(self, index: int) -> int:
        """Render page `index` (clamped to the page range) into the document; returns the page shown."""
        index = max(0, min(index, self.pages - 1))
        self.slot.content = self.prefix + self.fragments[index] + self.suffix
        self.footer_slot.content = PAGE_FOOTER_PREFIX + self.footer(index + 1, self.pages)
        self.page = index
        return index
