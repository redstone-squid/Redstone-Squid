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
from squid_ui.entity import ConversationType, EntityRef, EntityType, supports_entity
from squid_ui.extensions import ExtensionKind
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
from squid_ui.target_types import ClassicTarget, ComponentsV2Target, DiscordTarget, Renderable, RenderTarget
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


@dataclass(frozen=True, slots=True)
class Text(Renderable[DiscordTarget]):
    """One markdown paragraph. Truncates by default."""

    content: TextLike
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 0
    """Text-budget rank, compared across the whole message: higher priorities are granted in full before lower
    ones shrink, and ties share what is left. `Heading` defaults to 10, `Footer` to -10."""


@dataclass(frozen=True, slots=True)
class Heading(Renderable[DiscordTarget]):
    """A markdown heading: `level` leading `#`s on Discord, `h1`-`h6` (clamped) on HTML."""

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
    """Entries joined by `join`; spills to "…and N more" by default.

    `Spill` keeps or drops whole entries, so an entry may span lines. An `Alt` entry carries its
    own fallback ladder: under pressure the solver steps the largest entries down before it spills
    any, and `Alt.priority` decides which spill first. Survivors keep document order.
    """

    lines: tuple[TextLike | Alt, ...]
    join: str = "\n"
    overflow: Overflow = field(default_factory=Spill)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Time(Renderable[DiscordTarget]):
    """An instant drawn as a Discord `<t:unix:style>` tag, so the viewer's client localizes it."""

    instant: datetime
    style: str
    """One of Discord's timestamp style letters: `t T d D f F R`."""
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ZonedTime(Renderable[DiscordTarget]):
    """An instant shown in its own named timezone rather than the viewer's."""

    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class File(Renderable[ComponentsV2Target]):
    """A Components V2 file component; the bytes travel separately as the `asset:{asset_key}` plan resource."""

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Sep(Renderable[ComponentsV2Target]):
    """A Components V2 separator; `visible=False` keeps the spacing but drops the line."""

    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class LinkButton(Renderable[DiscordTarget]):
    """A link-style button; Discord opens `url` itself, so it carries no handler and needs no binding."""

    label: TextLike | None
    url: str
    emoji: EmojiLike | None = None
    disabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class PremiumButton(Renderable[DiscordTarget]):
    """A Discord premium button; Discord draws the SKU's name and price. Raises `ValueError` for `sku_id <= 0`."""

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
    """A button that presents a form.

    `form` names the binding the handler closes over, so a frontend can resolve the newest form for
    a submission that arrives late; the handler alone does not reveal which form it presents.
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
    """One choice in a `SelectMenu` or `RoutedSelect`; `value` is what the selection handler receives."""

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
    """Per-value bindings that replace `on_select`: a single selected value dispatches its route, and a
    value with no route, or a multi-value selection, is rejected as an invalid selection."""


@dataclass(frozen=True, slots=True)
class EntitySelect(Renderable[DiscordTarget]):
    """A user, role, channel or mentionable picker the frontend resolves; occupies its own row.

    Raises `ValueError` when `conversation_types` is set on a non-conversation picker or a default
    value's kind does not fit `entity_type`.
    """

    entity_type: EntityType
    on_select: EntitySelectionHandler
    key: str
    placeholder: TextLike | None = None
    default_values: tuple[EntityRef, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE

    def __post_init__(self) -> None:
        if self.conversation_types and self.entity_type is not EntityType.CONVERSATION:
            message = "conversation_types is only valid for conversation entity selects"
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
    """What an `Extension` lowers to once its adapter has prepared it; not for authors.

    The planner charges `text_cost` and `component_cost` without looking inside; `factory` yields
    the adapter's resource when the scene is drawn.
    """

    factory: Callable[[], object]
    text_cost: int = 0
    component_cost: int = 1
    kind: str = "discord.raw"
    version: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Boundary(Renderable[RenderTarget]):
    """A keyed child component the runtime renders and splices in before planning sees the tree.

    Typed for every target because it never reaches a planner; the child's own target parameter
    carries any restriction. Raises `ValueError` for an empty `key` or one containing `.`, which
    is the path separator.
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
    """The classic message's `content` field: the text a reply preview or push notification quotes.

    At most one per document, since a message has one such field; a Components V2 message has
    none. Defaults to `Never` because a notification quotes it and a clipped one misleads.
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
    """The embed author line; `url` makes the name a link."""

    name: CardText
    url: str | None = None
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class CardFooter:
    """The embed footer line, drawn beside `Card.timestamp` when both are set."""

    text: CardText
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class CardMedia:
    """An embed image or thumbnail. Discord's embed has no alt text; `description` survives for other targets."""

    url: str
    description: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Card(Renderable[ClassicTarget]):
    """One classic embed.

    `children` become the description, joined with blank lines in document order, so equal cards
    fingerprint equally. Every text slot takes an overflow policy through `CardText`. Properties
    Discord fills in itself (provider, video, unfurled URLs) are not offered, since a render
    cannot be diffed against text it did not write.
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
class Extension[PayloadT = object, RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """A node only an adapter registered for `kind` can draw; targets without one plan `fallback` instead."""

    kind: ExtensionKind[PayloadT, Any]
    version: int
    payload: PayloadT
    fallback: Node


@dataclass(frozen=True, slots=True)
class Row(Renderable[DiscordTarget]):
    """One action row as written; more buttons than the row limit is a planning error, not a reflow."""

    items: tuple[LinkButton | PremiumButton | Button | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class ControlGroup(Renderable[DiscordTarget]):
    """Buttons chunked into as many `Row`s as the target's row limit requires, in order."""

    items: tuple[LinkButton | PremiumButton | Button | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail(Renderable[ComponentsV2Target]):
    """A Components V2 thumbnail; only legal as a `Section.accessory`."""

    url: str
    description: TextLike | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class GalleryItem:
    """One gallery image; a bare URL in `Gallery.items` becomes one with no description."""

    url: str
    description: TextLike | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Gallery(Renderable[ComponentsV2Target]):
    """One media gallery as written; more items than the gallery limit is a planning error, not a split."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "items", tuple(GalleryItem(item) if isinstance(item, str) else item for item in self.items)
        )


@dataclass(frozen=True, slots=True)
class MediaCollection(Renderable[ComponentsV2Target]):
    """Images chunked into as many `Gallery` nodes as the target's gallery limit requires, in order."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "items", tuple(GalleryItem(item) if isinstance(item, str) else item for item in self.items)
        )


@dataclass(frozen=True, slots=True)
class Section(Renderable[ComponentsV2Target]):
    """Up to three text nodes beside an accessory; a fourth text, or a `Paginate` policy inside, is a planning error."""

    texts: tuple[Text | Heading | Footer, ...]
    accessory: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class Panel(Renderable[ComponentsV2Target]):
    """A Components V2 container; `accent` is its left-edge colour."""

    children: tuple[Node, ...]
    accent: Color | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Budget[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Invisible group that reserves text for its children as a block before priorities are weighed.

    `minimum` is held back first; `preferred` is the demand; above `preferred + stretch` the group asks
    for `preferred` only. Draws from one text pool: children spanning two pools is a layout error.
    """

    children: tuple[Node, ...]
    minimum: int
    preferred: int
    stretch: int = 0
    best_effort: bool = False
    """Let the allocator breach `minimum` (noted as `BEST_EFFORT_FLOOR`) instead of failing the layout."""


@dataclass(frozen=True, slots=True)
class Break[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Invisible group that region pagination treats as one item; drawn flat."""

    children: tuple[Node, ...]
    unbreakable: bool = False
    """Never split the group across pages; a group wider than one page is then an `UnsolvableLayoutError`."""
    keep_with_next: bool = False
    """No page break directly after the group, so it never ends a page; a leading heading gets this implicitly."""


class Fidelity(StrEnum):
    """What a `Variant` costs the reader; priced separately from its position in the ladder.

    Rung order is preference, not loss: a later rung may be exact (paginating loses nothing), so
    the solver reads loss from here. `strict=True` rejects any chosen rung that is not `EXACT`.
    """

    EXACT = "exact"
    """Every authored element survives, in a shape the target renders faithfully."""

    REFORMATTED = "reformatted"
    """Every element survives in a different shape: a table as lines, fields as prose."""

    LOSSY = "lossy"
    """Something the author wrote is not shown at all."""


@dataclass(frozen=True, slots=True)
class Variant[RenderTargetT = DiscordTarget]:
    """One rung of a `Variants` ladder. Raises `ValueError` when `nodes` is empty.

    `nodes` are spliced into the parent rather than wrapped, so a rung can be several rows
    without spending a container. A rung that reformats or discards content must say so in
    `fidelity`; the default assumes a hand-written alternative is a smaller faithful shape.
    """

    nodes: tuple[Node, ...]
    requires: frozenset[str] = frozenset()
    """Capability names the target must have; a rung missing any is dropped from the ladder before planning."""
    fidelity: Fidelity = Fidelity.EXACT

    def __post_init__(self) -> None:
        if not self.nodes:
            message = "Variant needs at least one node"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Variants[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """An ordered ladder of shapes for one region; the only way to give up *components* under pressure.

    Overflow policies shrink text only. A ladder lets the solver trade a button panel for one
    select, or a gallery for a link row. Raises `ValueError` when `variants` is empty.

    Rungs whose `requires` the target lacks are dropped first. The solver opens every ladder at
    rung 0 and searches rung assignments best-first, measuring each candidate together with text
    loss. Position prices preference and `Fidelity` prices loss, so a later exact rung beats an
    earlier reformatted one. A nested ladder is searchable only while its ancestor's selected
    rung exposes it; stepping the ancestor reopens whatever the new rung holds at rung 0.
    """

    variants: tuple[Variant[Any], ...]
    priority: int = 0
    """Compared across the whole document, not among siblings: lower-priority ladders step first, and
    equal priorities step breadth-first, each reaching rung 1 before any reaches rung 2."""

    def __post_init__(self) -> None:
        if not self.variants:
            message = "Variants needs at least one variant"
            raise ValueError(message)

    @classmethod
    @overload
    def of[FirstT, SecondT](
        cls,
        first: Renderable[FirstT] | Variant[FirstT],
        second: Renderable[SecondT] | Variant[SecondT],
        /,
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT](
        cls,
        first: Renderable[FirstT] | Variant[FirstT],
        second: Renderable[SecondT] | Variant[SecondT],
        third: Renderable[ThirdT] | Variant[ThirdT],
        /,
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT, FourthT](
        cls,
        first: Renderable[FirstT] | Variant[FirstT],
        second: Renderable[SecondT] | Variant[SecondT],
        third: Renderable[ThirdT] | Variant[ThirdT],
        fourth: Renderable[FourthT] | Variant[FourthT],
        /,
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT | FourthT]: ...

    @classmethod
    @overload
    def of[FirstT, SecondT, ThirdT, FourthT, FifthT](
        cls,
        first: Renderable[FirstT] | Variant[FirstT],
        second: Renderable[SecondT] | Variant[SecondT],
        third: Renderable[ThirdT] | Variant[ThirdT],
        fourth: Renderable[FourthT] | Variant[FourthT],
        fifth: Renderable[FifthT] | Variant[FifthT],
        /,
        *,
        priority: int = 0,
    ) -> Variants[FirstT | SecondT | ThirdT | FourthT | FifthT]: ...

    @classmethod
    @overload
    def of(cls, *rungs: Renderable[Any] | Variant[Any], priority: int = 0) -> Variants[Any]: ...

    @classmethod
    def of(cls, *rungs: Renderable[Any] | Variant[Any], priority: int = 0) -> Variants[Any]:
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
    | Button
    | LinkButton
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


def as_nodes(rendered: Node | Sequence[Node]) -> list[Node]:
    """Normalize a render result — one node or a sequence of them — to a list."""
    return list(rendered) if isinstance(rendered, Sequence) else [rendered]
