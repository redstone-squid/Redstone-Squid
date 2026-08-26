"""Widget-level IR: Discord-shaped nodes carrying overflow policies.

The semantic layer compiles to these; views needing exact control write them directly. Nodes
are immutable descriptions. The planner fits them to target budgets and renderers draw the
resulting scene — authors never do budget arithmetic.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast, overload

from squid_ui.emoji import EmojiLike, normalize_emoji
from squid_ui.entity import ChannelType, EntityRef, EntityType, supports_entity
from squid_ui.forms import FormBinding
from squid_ui.guards import Guard
from squid_ui.interactions import (
    ActionBinding,
    ActionMode,
    BusySpec,
    EntitySelectionHandler,
    PressHandler,
    SelectionHandler,
)
from squid_ui.primitives.constraints import Alt, Never, Overflow, Spill, Truncate
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.target_types import ClassicTarget, ComponentsV2Target, DiscordTarget, Renderable
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


@dataclass(frozen=True, slots=True)
class Text(Renderable[DiscordTarget]):
    content: TextLike
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Heading(Renderable[DiscordTarget]):
    content: TextLike
    level: int = 2
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 10


@dataclass(frozen=True, slots=True)
class Footer(Renderable[DiscordTarget]):
    """Small (`-#`) text at the card's foot; first to shrink by default."""

    content: TextLike
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = -10


@dataclass(frozen=True, slots=True)
class Code(Renderable[DiscordTarget]):
    """Fenced code block; embedded fences are escaped so content cannot break out."""

    content: TextLike
    lang: str = ""
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Lines(Renderable[DiscordTarget]):
    """A list of entries joined by ``join``; spills to "…and N more" by default.

    Entries may span multiple lines themselves — Spill keeps or drops whole entries. An entry
    may also be an :class:`~squid_ui.primitives.constraints.Alt` carrying a degradation ladder:
    under pressure the solver steps the largest entries down their fallbacks before it spills
    any entry. Each :class:`~squid_ui.primitives.constraints.Alt` may carry a drop priority; lower
    priorities disappear first, while surviving entries keep document order.
    """

    lines: tuple[TextLike | Alt, ...]
    join: str = "\n"
    overflow: Overflow = field(default_factory=Spill)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Time(Renderable[DiscordTarget]):
    """A typed instant retained through scene conversion."""

    instant: datetime
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ZonedTime(Renderable[DiscordTarget]):
    """An exact instant visibly retained with its named timezone."""

    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class File(Renderable[ComponentsV2Target]):
    """A visible file component backed by a separately carried asset resource."""

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Sep(Renderable[ComponentsV2Target]):
    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class LinkButton(Renderable[DiscordTarget]):
    label: TextLike | None
    url: str
    emoji: EmojiLike | None = None
    disabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class PremiumButton(Renderable[DiscordTarget]):
    """A Discord premium button identified solely by its application SKU."""

    sku_id: int

    def __post_init__(self) -> None:
        if self.sku_id <= 0:
            message = "PremiumButton sku_id must be positive"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Button(Renderable[DiscordTarget]):
    """An interactive button whose handler runs through the mount's dispatch funnel."""

    label: TextLike | None
    on_click: PressHandler
    key: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: EmojiLike | None = None
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE
    guard: Guard | None = None
    busy: BusySpec | None = None
    record: History | None = None
    """Enter this press in history under `label` before `on_click` runs."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class FormButton(Button):
    """A button that presents a form, carrying the binding its handler closes over.

    The handler alone is opaque: a frontend holding it cannot tell which form it presents,
    so it cannot resolve the newest one for a submission that arrived late. This states it.
    """

    form: FormBinding | None = None


@dataclass(frozen=True, slots=True)
class RoutedButton(Renderable[DiscordTarget]):
    """A button whose route id *is* its state, dispatched by a router rather than a mount.

    Carries no handler, so it needs no binding and survives the process that drew it: a
    sessionless document may hold one, and a mount's policies (author lock, generation
    checks) do not reach it even when it sits inside a mounted message. Build the id with
    a `squid_ui.routing.Route` rather than by hand.
    """

    label: TextLike | None
    route_id: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: EmojiLike | None = None
    disabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class Option:
    label: TextLike
    value: str
    description: TextLike | None = None
    default: bool = False
    emoji: EmojiLike | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class SelectMenu(Renderable[DiscordTarget]):
    """A string select; occupies its own row when materialized."""

    options: tuple[Option, ...]
    on_select: SelectionHandler
    key: str
    placeholder: TextLike | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE
    routes: Mapping[str, ActionBinding] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntitySelect(Renderable[DiscordTarget]):
    """A frontend-resolved entity picker; occupies its own row."""

    entity_type: EntityType
    on_select: EntitySelectionHandler
    key: str
    placeholder: TextLike | None = None
    default_values: tuple[EntityRef, ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE

    def __post_init__(self) -> None:
        if self.channel_types and self.entity_type is not EntityType.CHANNEL:
            message = "channel_types is only valid for channel entity selects"
            raise ValueError(message)
        if any(not supports_entity(self.entity_type, value.kind) for value in self.default_values):
            message = f"default value is incompatible with {self.entity_type.value} entity select"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RoutedSelect(Renderable[DiscordTarget]):
    """A string select dispatched by its stable route id rather than a mount binding."""

    options: tuple[Option, ...]
    route_id: str
    placeholder: TextLike | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class RawItem(Renderable[DiscordTarget]):
    """Internal prepared target item retained until scene drawing."""

    factory: Callable[[], object]
    text_cost: int = 0
    component_cost: int = 1
    kind: str = "discord.raw"
    version: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Boundary(Renderable[DiscordTarget]):
    """A keyed component boundary expanded before portable planning.

    Named for what it is rather than what it draws to. It has never had anything to do with
    a Discord embed, and a node called ``Embed`` in the same union as real embed rendering
    is a trap for every future reader.
    """

    component: object
    key: str

    def __post_init__(self) -> None:
        if not self.key:
            message = "Boundary key must not be empty"
            raise ValueError(message)
        if "." in self.key:
            message = "Boundary key must not contain '.'"
            raise ValueError(message)


# --- Classic message structure --------------------------------------------------------------
#
# These are as exact as `Row` and `Section` are, and gated the same way: a target that lacks
# `message.content` or `layout.embed` rejects them during validation. They live in the shared
# node union rather than a parallel IR so that one `Variants` ladder can offer a V2 rung and a
# classic rung for the same region, and so `resolve_variants` and measurement stay single
# implementations.


@dataclass(frozen=True, slots=True)
class Content(Renderable[ClassicTarget]):
    """The classic message's `content` field: the text a reply preview or push shows.

    A Components V2 message has no `content` at all, which is the whole reason the classic
    target is a permanent capability rather than a migration ramp. At most one may appear in
    a document, because a message has exactly one such field.

    Defaults to `Never` because content is usually the part that must survive: it is what a
    notification quotes, and silently shortening it defeats the reason it was written.
    """

    content: TextLike
    overflow: Overflow = field(default_factory=Never)
    priority: int = 0


type CardText = TextLike | Text
"""A card slot's text: a bare string, or a `Text` carrying an overflow policy.

A bare string means `Never` — a title or a field name is written to be read whole, and
quietly clipping one is worse than telling the author it does not fit. An author who would
rather it shrank says so by writing `Text(value, overflow=Truncate())`.
"""


def card_text(value: CardText) -> Text:
    """Normalize a card slot to a `Text` node, defaulting a bare string to `Never`."""
    return value if isinstance(value, Text) else Text(value, overflow=Never())


@dataclass(frozen=True, slots=True)
class CardField:
    """One embed field. A nested value, never a legal root node."""

    name: CardText
    value: CardText
    inline: bool = False


@dataclass(frozen=True, slots=True)
class CardAuthor:
    name: CardText
    url: str | None = None
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class CardFooter:
    text: CardText
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class CardMedia:
    """An embed image or thumbnail. The description is kept even where Discord drops it."""

    url: str
    description: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Card(Renderable[ClassicTarget]):
    """One embed: a titled, coloured, field-structured block beside the message text.

    ``children`` are description blocks, joined with blank lines in document order — one
    deterministic joining rule, so the same card always produces the same description and the
    same fingerprint.

    Every text-bearing slot takes the ordinary overflow policies through :data:`CardText`.
    Server-generated embed properties — provider, video, and anything Discord fills in from a
    URL it unfurls — are not offered, because Squid cannot own what it did not write.
    """

    children: tuple[Node, ...] = ()
    title: CardText | None = None
    url: str | None = None
    fields: tuple[CardField, ...] = ()
    footer: CardFooter | None = None
    author: CardAuthor | None = None
    accent: Color | None = None
    image: CardMedia | None = None
    thumbnail: CardMedia | None = None
    timestamp: ZonedDateTime | datetime | None = None


@dataclass(frozen=True, slots=True)
class Extension[ModeT = DiscordTarget](Renderable[ModeT]):
    """Target extension with a mandatory portable fallback."""

    kind: str
    version: int
    payload: object
    fallback: Node


@dataclass(frozen=True, slots=True)
class Row(Renderable[DiscordTarget]):
    """An exact target row; invalid local structure is a planning error."""

    items: tuple[LinkButton | PremiumButton | Button | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class ControlGroup(Renderable[DiscordTarget]):
    """Buttons automatically arranged into as many valid target rows as needed."""

    items: tuple[LinkButton | PremiumButton | Button | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail(Renderable[ComponentsV2Target]):
    url: str
    description: TextLike | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class GalleryItem:
    """One gallery image with its accessible description and spoiler state."""

    url: str
    description: TextLike | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Gallery(Renderable[ComponentsV2Target]):
    """One exact target gallery."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "items", tuple(GalleryItem(item) if isinstance(item, str) else item for item in self.items)
        )


@dataclass(frozen=True, slots=True)
class MediaCollection(Renderable[ComponentsV2Target]):
    """Media automatically arranged into valid target galleries."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "items", tuple(GalleryItem(item) if isinstance(item, str) else item for item in self.items)
        )


@dataclass(frozen=True, slots=True)
class Section(Renderable[ComponentsV2Target]):
    """Up to three text nodes beside an accessory; extra texts are dropped with a note."""

    texts: tuple[Text | Heading | Footer, ...]
    accessory: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class Panel(Renderable[ComponentsV2Target]):
    """A Container: children grouped under an optional accent colour."""

    children: tuple[Node, ...]
    accent: Color | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Budget[ModeT = DiscordTarget](Renderable[ModeT]):
    """Transparent group carrying an author-sized character reservation and ceiling."""

    children: tuple[Node, ...]
    minimum: int
    preferred: int
    stretch: int = 0
    best_effort: bool = False


@dataclass(frozen=True, slots=True)
class Break[ModeT = DiscordTarget](Renderable[ModeT]):
    """Transparent group carrying region-break annotations through semantic lowering."""

    children: tuple[Node, ...]
    unbreakable: bool = False
    keep_with_next: bool = False


class Fidelity(StrEnum):
    """How faithfully one variant reproduces the region it represents.

    Rung order is a *preference*, not a loss ladder. A later rung may be a perfectly
    faithful alternative — paginating a long region loses nothing — so the solver must be
    told which rungs actually cost the reader something rather than inferring it from
    position. Without this, an exact but late rung would be priced as loss and a lossy
    early rung would be priced as free, and `strict=True` could reject neither honestly.
    """

    EXACT = "exact"
    """Every authored element survives, in a shape the target renders faithfully."""

    REFORMATTED = "reformatted"
    """Every element survives in a different shape: a table as lines, fields as prose."""

    LOSSY = "lossy"
    """Something the author wrote is not shown at all."""


@dataclass(frozen=True, slots=True)
class Variant[ModeT = DiscordTarget]:
    """One structural representation of a region and the capabilities it requires.

    ``nodes`` is a tuple because a variant may lower to several nodes — an ControlGroup becomes
    one Row per five buttons — and splicing them into the parent is exact where wrapping them
    in a Panel would invent the very container component the ladder exists to save.

    ``fidelity`` defaults to :attr:`Fidelity.EXACT` because most alternatives are: an author
    writing a ladder by hand is usually offering a smaller faithful shape. A library adapter
    offering a rung that reformats or discards content must say so explicitly.
    """

    nodes: tuple[Node, ...]
    requires: frozenset[str] = frozenset()
    fidelity: Fidelity = Fidelity.EXACT

    def __post_init__(self) -> None:
        if not self.nodes:
            message = "Variant needs at least one node"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Variants[ModeT = DiscordTarget](Renderable[ModeT]):
    """An ordered ladder of structural representations for one region.

    Overflow policies shrink *text*; nothing they do returns a component, so a document with
    too many components would otherwise only be reportable. A ladder gives the solver
    something to give up: a button panel stepping to one select, a gallery to a link row.

    A rung's *position* prices preference; its :class:`Fidelity` prices loss. A later exact
    rung therefore beats an earlier reformatted one, and `strict=True` rejects the reformatted
    or lossy rung it would otherwise have to accept silently.

    Rungs unsupported by the target are dropped at planning time; the survivors form a budget
    ladder. The solver opens every ladder at rung 0 and searches reachable rung assignments
    best-first under component pressure. Every candidate is measured together with text loss,
    so an ineffective early ladder cannot force a later sibling to degrade as well.

    Two rules follow from stepping being a whole-tree decision. ``priority`` compares
    **globally**, not among siblings: lower-priority loss is cheaper, and equal priorities
    compare breadth-first, each reaching rung 1 before any reaches rung 2. A nested ladder only
    becomes searchable once its ancestor's *selected* rung exposes it; stepping the ancestor
    abandons it and opens whatever the new rung holds at rung 0.
    """

    variants: tuple[Variant[Any], ...]
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.variants:
            message = "Variants needs at least one variant"
            raise ValueError(message)

    @classmethod
    @overload
    def of[FirstT, SecondT](  # pyrefly: ignore[inconsistent-overload]
        cls,
        first: PrimitiveNode[FirstT] | Variant[FirstT],
        second: PrimitiveNode[SecondT] | Variant[SecondT],
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT](  # pyrefly: ignore[inconsistent-overload]
        cls,
        first: PrimitiveNode[FirstT] | Variant[FirstT],
        second: PrimitiveNode[SecondT] | Variant[SecondT],
        third: PrimitiveNode[ThirdT] | Variant[ThirdT],
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT, FourthT](  # pyrefly: ignore[inconsistent-overload]
        cls,
        first: PrimitiveNode[FirstT] | Variant[FirstT],
        second: PrimitiveNode[SecondT] | Variant[SecondT],
        third: PrimitiveNode[ThirdT] | Variant[ThirdT],
        fourth: PrimitiveNode[FourthT] | Variant[FourthT],
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT | FourthT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT, FourthT, FifthT](  # pyrefly: ignore[inconsistent-overload]
        cls,
        first: PrimitiveNode[FirstT] | Variant[FirstT],
        second: PrimitiveNode[SecondT] | Variant[SecondT],
        third: PrimitiveNode[ThirdT] | Variant[ThirdT],
        fourth: PrimitiveNode[FourthT] | Variant[FourthT],
        fifth: PrimitiveNode[FifthT] | Variant[FifthT],
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT | FourthT | FifthT]: ...

    @classmethod
    @overload
    def of(cls, *rungs: PrimitiveNode[Any] | Variant[Any], priority: int = 0) -> Variants[Any]: ...

    @classmethod
    def of(cls, *rungs: PrimitiveNode[Any] | Variant[Any], priority: int = 0) -> Variants[Any]:
        """Build a ladder from bare nodes, wrapping each in an exact, capability-free Variant."""
        return cls(
            tuple(rung if isinstance(rung, Variant) else Variant((cast(Node, rung),)) for rung in rungs),
            priority,
        )


type Node = (
    Text
    | Heading
    | Footer
    | Code
    | Lines
    | Time
    | ZonedTime
    | File
    | Sep
    | Row
    | ControlGroup
    | SelectMenu
    | EntitySelect
    | RoutedSelect
    | RoutedButton
    | PremiumButton
    | Thumbnail
    | Gallery
    | MediaCollection
    | Section
    | Panel
    | Budget
    | Break
    | RawItem
    | Boundary
    | Card
    | Content
    | Extension
    | Variants
)

type PrimitiveNode[ModeT = DiscordTarget] = Renderable[ModeT]


def as_nodes(rendered: Node | Sequence[Node]) -> list[Node]:
    """Normalize a render result — one node or a sequence of them — to a list."""
    return list(rendered) if isinstance(rendered, Sequence) else [rendered]
