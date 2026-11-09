"""Describe exact Discord widgets and the ways their content may shrink to fit.

The Discord planner converts semantic layout nodes into these primitives; applications
can also author them directly when they need a particular widget arrangement. Overflow
policies govern text shortening, while Variants offers alternative widget arrangements.
The planner fits the chosen primitives to target limits and produces a Scene for rendering.
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
    from squid_ui.runtime.definition import Component
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
class Button[ContextT = object](Renderable[DiscordTarget, ContextT]):
    """An interactive button whose handler runs through the mount's dispatch funnel."""

    label: TextLike | None
    on_click: PressHandler[ContextT]
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
class FormButton[ContextT = object](Button[ContextT]):
    """A form-opening button with the form binding its handler uses.

    Keeping the binding separate from the callback lets the frontend resolve the
    current form when a submission arrives after the form has been refreshed.
    """

    form: FormBinding | None = None


@dataclass(frozen=True, slots=True)
class RoutedButton(Renderable[DiscordTarget]):
    """A button dispatched by a stable route ID instead of a mounted Python callback.

    The host can register the route after a restart and recover application state from
    its parameters. A mount's author lock and generation checks do not apply to routed
    buttons, even inside a mounted message; the route handler must check authorization.
    Build the ID with squid_ui.routing.Route so its format and length are validated.
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
class SelectMenu[ContextT = object](Renderable[DiscordTarget, ContextT]):
    """A string select; occupies its own row when materialized."""

    options: tuple[Option, ...]
    on_select: SelectionHandler[ContextT]
    key: str
    placeholder: TextLike | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE
    routes: Mapping[str, ActionBinding] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntitySelect[ContextT = object](Renderable[DiscordTarget, ContextT]):
    """A frontend-resolved entity picker; occupies its own row."""

    entity_type: EntityType
    on_select: EntitySelectionHandler[ContextT]
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
    """Internal prepared target item retained until scene drawing."""

    factory: Callable[[], object]
    text_cost: int = 0
    component_cost: int = 1
    kind: str = "discord.raw"
    version: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Boundary[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    """Place a child component in the layout under its own control and presentation keys.

    The runtime replaces this node with the child's rendered layout before planning.
    Component.boundary constructs it while preserving the child's target requirements.
    """

    component: Component[RenderTargetT, ContextT]
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
    """The classic message's content field, also used in reply previews and notifications.

    At most one may appear in a document, and Components V2 targets reject it.
    The default `Never` policy reserves its full text and makes shortening visible
    in planning diagnostics.
    """

    content: TextLike
    overflow: Overflow = field(default_factory=Never)
    priority: int = 0


type CardText = TextLike | Text
"""Text for an embed slot, optionally wrapped in `Text` to choose an overflow policy.

Bare text uses `Never`. Use `Text(value, overflow=Truncate())` when shortening is
acceptable.
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
class Extension[PayloadT = object, RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Target extension with a mandatory portable fallback."""

    kind: ExtensionKind[PayloadT, Any]
    version: int
    payload: PayloadT
    fallback: Node


@dataclass(frozen=True, slots=True)
class Row[ContextT = object](Renderable[DiscordTarget, ContextT]):
    """An exact target row; invalid local structure is a planning error."""

    items: tuple[LinkButton | PremiumButton | Button[ContextT] | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class ControlGroup[ContextT = object](Renderable[DiscordTarget, ContextT]):
    """Buttons automatically arranged into as many valid target rows as needed."""

    items: tuple[LinkButton | PremiumButton | Button[ContextT] | RoutedButton | RawItem, ...]


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


def normalize_gallery_item(value: str | GalleryItem) -> GalleryItem:
    """Promote a shorthand image URL to a complete gallery item."""
    return GalleryItem(value) if isinstance(value, str) else value


@dataclass(frozen=True, slots=True)
class Gallery(Renderable[ComponentsV2Target]):
    """One exact target gallery."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(normalize_gallery_item(item) for item in self.items))


@dataclass(frozen=True, slots=True)
class MediaCollection(Renderable[ComponentsV2Target]):
    """Media automatically arranged into valid target galleries."""

    items: tuple[str | GalleryItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(normalize_gallery_item(item) for item in self.items))


@dataclass(frozen=True, slots=True)
class Section[ContextT = object](Renderable[ComponentsV2Target, ContextT]):
    """An exact Components V2 section with up to three texts beside an accessory.

    Target planning rejects excess text slots. The lower-level measurement helper
    can clamp them with a diagnostic, so callers should not rely on that fallback
    when authoring a document.
    """

    texts: tuple[Text | Heading | Footer, ...]
    accessory: Thumbnail | LinkButton | PremiumButton | Button[ContextT] | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class Panel[ContextT = object](Renderable[ComponentsV2Target, ContextT]):
    """A Container: children grouped under an optional accent colour."""

    children: tuple[Node[ContextT], ...]
    accent: Color | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Budget[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Group text under an explicit character reservation and preferred size.

    `minimum` reserves space before discretionary allocation. `preferred` is the
    normal target size; `stretch` permits extra space when the content fits within
    that allowance. `best_effort` lets the minimum be breached with a degradation
    note instead of a budget-floor failure.
    """

    children: tuple[Node, ...]
    minimum: int
    preferred: int
    stretch: int = 0
    best_effort: bool = False


@dataclass(frozen=True, slots=True)
class Break[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Transparent group carrying region-break annotations through semantic lowering."""

    children: tuple[Node, ...]
    unbreakable: bool = False
    keep_with_next: bool = False


class Fidelity(StrEnum):
    """How much authored content a variant preserves, independently of its position.

    Later variants can still be exact, such as a paginated representation of all
    content. Mark reformatted or omitted content explicitly so planning can rank
    the loss and strict mode can reject it.
    """

    EXACT = "exact"
    """Every authored element survives, in a shape the target renders faithfully."""

    REFORMATTED = "reformatted"
    """Every element survives in a different shape: a table as lines, fields as prose."""

    LOSSY = "lossy"
    """Something the author wrote is not shown at all."""


@dataclass(frozen=True, slots=True)
class Variant[RenderTargetT = DiscordTarget]:
    """One nonempty representation of a region and the capabilities it requires.

    `nodes` are spliced into the parent, so a variant can produce several rows
    without adding a container. `fidelity` defaults to `EXACT`; authors must mark
    alternatives that reformat or discard content.
    """

    nodes: tuple[Node, ...]
    requires: frozenset[str] = frozenset()
    fidelity: Fidelity = Fidelity.EXACT

    def __post_init__(self) -> None:
        if not self.nodes:
            message = "Variant needs at least one node"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Variants[RenderTargetT = DiscordTarget](Renderable[RenderTargetT]):
    """Ordered representations the planner can choose to fit one region.

    Text overflow policies shorten text; variants can also change component
    structure, such as replacing a button panel with a select. Unsupported
    variants are removed before budget search.

    Position expresses preference, while `Fidelity` describes loss. Priority
    applies across the document: losing lower-priority content is cheaper.
    Among equally eligible ladders, stepping proceeds breadth-first. A nested
    ladder participates only while its parent's chosen variant exposes it.

    The planner measures candidate choices together with text degradation. Its
    search is bounded, so exhausting the search budget can select the best fit
    found rather than the best possible combination; the plan reports this.
    """

    variants: tuple[Variant[Any], ...]
    priority: int = 0

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


type Node[ContextT = object] = (
    Text
    | Heading
    | Footer
    | Code
    | Lines
    | Time
    | ZonedTime
    | File
    | Sep
    | Row[ContextT]
    | ControlGroup[ContextT]
    | Button[ContextT]
    | LinkButton
    | SelectMenu[ContextT]
    | EntitySelect[ContextT]
    | RoutedSelect
    | RoutedButton
    | PremiumButton
    | Thumbnail
    | Gallery
    | MediaCollection
    | Section[ContextT]
    | Panel[ContextT]
    | Budget
    | Break
    | RawItem
    | Boundary[Any, ContextT]
    | Card
    | Content
    | Extension
    | Variants
)


def as_nodes(rendered: Node | Sequence[Node]) -> list[Node]:
    """Normalize a render result — one node or a sequence of them — to a list."""
    return list(rendered) if isinstance(rendered, Sequence) else [rendered]
