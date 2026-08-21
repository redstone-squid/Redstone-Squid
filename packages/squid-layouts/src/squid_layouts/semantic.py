"""Frontend-neutral semantic layout vocabulary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from squid_layouts.actions import ActionEvent, ActionPolicy
from squid_layouts.primitives.nodes import Node as PrimitiveNode
from squid_layouts.primitives.styles import Color
from squid_layouts.text import TextLike


class ActionDisplay(StrEnum):
    AUTO = "auto"
    INDIVIDUAL = "individual"
    GROUPED = "grouped"


class NavigationDisplay(StrEnum):
    AUTO = "auto"
    INDIVIDUAL = "individual"
    GROUPED = "grouped"


class ItemDisplay(StrEnum):
    AUTO = "auto"
    OVERVIEW = "overview"
    OPENED = "opened"


class TableDisplay(StrEnum):
    AUTO = "auto"
    TABULAR = "tabular"
    RECORDS = "records"


class DetailLevel(StrEnum):
    AUTO = "auto"
    FULL = "full"
    SUMMARY = "summary"


class MediaDisplay(StrEnum):
    AUTO = "auto"
    COLLECTION = "collection"
    FEATURED = "featured"


class Flexibility(IntEnum):
    FLEXIBLE = 0
    NORMAL = 1
    STABLE = 2


class Importance(IntEnum):
    LOW = -100
    NORMAL = 0
    HIGH = 100


class Emphasis(StrEnum):
    SUBTLE = "subtle"
    NORMAL = "normal"
    STRONG = "strong"


class Tone(StrEnum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


# --- Who owns a node's value -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Controlled[ValueT, EventT]:
    """The author owns this value: authoritative on every render, never written by the engine."""

    value: ValueT
    on_change: Callable[[EventT], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Managed[ValueT]:
    """The engine owns this value, in the presentation session under the node's key.

    ``initial`` is a seed, not a value: it applies on a session miss and is ignored from
    then on. An author who needs their value to keep winning wants `Controlled`.
    """

    initial: ValueT


type Ownership[ValueT, EventT] = Controlled[ValueT, EventT] | Managed[ValueT]
"""Every stateful semantic node takes one of these, and it is the whole ownership story.

Ownership is a value rather than something inferred from whether a handler was passed,
so a node cannot be half-controlled and the mode is readable at the call site.
"""


type ChoiceOwnership = Ownership[tuple[str, ...], ChoiceEvent]
type ItemOwnership = Ownership[str | None, OpenEvent[str | None]]
type DisclosureOwnership = Ownership[bool, OpenEvent[bool]]
type NavOwnership = Ownership[str | None, NavigateEvent]

# The engine-managed default of each stateful node, named for the state it seeds.
UNSELECTED: ChoiceOwnership = Managed(())
UNOPENED: ItemOwnership = Managed(None)
CLOSED: DisclosureOwnership = Managed(initial=False)
FIRST_DESTINATION: NavOwnership = Managed(None)


@dataclass(frozen=True, slots=True)
class Group:
    children: tuple[LayoutNode, ...]


@dataclass(frozen=True, slots=True)
class Stack:
    children: tuple[LayoutNode, ...]


@dataclass(frozen=True, slots=True)
class Cluster:
    children: tuple[LayoutNode, ...]


@dataclass(frozen=True, slots=True)
class Section:
    """A titled block of related content.

    ``accent`` is an explicit house-colour override, not a semantic fact: it says "paint
    this the brand's green", which is chrome the framework has no opinion about. Colour
    that *means* something — advisory, warning, failed — belongs on `Aside` or `Status`
    via `Tone`, which every target maps for itself. Reach for ``accent`` when the exact
    value is data (a guild's configured colour) or house style, and for `Aside` otherwise.

    ``thumbnail`` is a lead image shown beside the heading. With no heading there is
    nothing to sit beside, so it lowers to a leading single-image gallery instead.
    """

    children: tuple[LayoutNode, ...]
    heading: TextLike | None = None
    accent: Color | None = None
    thumbnail: str | None = None


@dataclass(frozen=True, slots=True)
class Article:
    """A self-contained block that stands on its own; see `Section` for the extras."""

    children: tuple[LayoutNode, ...]
    heading: TextLike | None = None
    accent: Color | None = None
    thumbnail: str | None = None


@dataclass(frozen=True, slots=True)
class Aside:
    children: tuple[LayoutNode, ...]
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class Heading:
    content: TextLike
    level: int = 2
    importance: Importance = Importance.HIGH


@dataclass(frozen=True, slots=True)
class Paragraph:
    content: TextLike
    importance: Importance = Importance.NORMAL


@dataclass(frozen=True, slots=True)
class ListItem:
    key: str
    content: TextLike
    importance: Importance = Importance.NORMAL


@dataclass(frozen=True, slots=True)
class List:
    items: tuple[ListItem, ...]
    key: str
    ordered: bool = False
    page_size: int | None = None


@dataclass(frozen=True, slots=True)
class Field:
    """One labelled value.

    ``fallbacks`` are shorter forms of ``value``, tried in order when the block is under
    budget pressure — a count where the full form is a hundred links. A field steps down
    its own ladder independently of its neighbours and is never dropped whole.
    """

    key: str
    label: TextLike
    value: TextLike
    importance: Importance = Importance.NORMAL
    fallbacks: tuple[TextLike, ...] = ()


@dataclass(frozen=True, slots=True)
class Fields:
    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    heading: TextLike
    importance: Importance = Importance.NORMAL


@dataclass(frozen=True, slots=True)
class TableRow:
    key: str
    cells: tuple[TextLike, ...]


@dataclass(frozen=True, slots=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[TableRow, ...]
    key: str
    display: TableDisplay = TableDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Note:
    """Small print: an id, a timestamp, a caveat the reader may skip."""

    content: TextLike
    importance: Importance = Importance.LOW


@dataclass(frozen=True, slots=True)
class Quote:
    content: TextLike
    attribution: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Code:
    content: str
    language: str = ""


@dataclass(frozen=True, slots=True)
class MediaItem:
    key: str
    url: str
    description: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Figure:
    media: MediaItem
    caption: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Media:
    items: tuple[MediaItem, ...]
    key: str
    display: MediaDisplay = MediaDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Details:
    key: str
    summary: TextLike
    children: tuple[LayoutNode, ...]
    open: DisclosureOwnership = CLOSED


@dataclass(frozen=True, slots=True)
class Status:
    content: TextLike
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL


@dataclass(frozen=True, slots=True)
class Progress:
    value: float
    label: TextLike | None = None
    maximum: float = 1.0


@dataclass(frozen=True, slots=True)
class Measure:
    value: int | float | str
    label: TextLike
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class Action:
    key: str
    label: TextLike
    on_trigger: Callable[[ActionEvent], Awaitable[None]]
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True
    allow_grouping: bool | None = None
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class Link:
    key: str
    label: TextLike
    url: str
    emphasis: Emphasis = Emphasis.NORMAL


@dataclass(frozen=True, slots=True)
class RoutedAction:
    """A control whose custom id is its state, dispatched by a router rather than a mount.

    For the buttons on mass-posted cards that must still work after a restart: no
    in-process handler, so a sessionless document may carry one. Build ``route_id`` with
    a `squid_layouts.routing.Route`, which validates it against Discord's budget at
    authoring time rather than at send time.

    `Action` remains the right node whenever a session is already in play; this one buys
    survival at the price of every guarantee the mount's funnel provides.
    """

    key: str
    label: TextLike
    route_id: str
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True


@dataclass(frozen=True, slots=True)
class ActionGroup:
    key: str
    actions: tuple[Action | Link | RoutedAction, ...]
    label: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Actions:
    items: tuple[Action | Link | RoutedAction | ActionGroup, ...]
    key: str
    display: ActionDisplay = ActionDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    label: TextLike
    description: TextLike | None = None
    available: bool = True


@dataclass(frozen=True, slots=True)
class ChoiceEvent(ActionEvent):
    selected: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenEvent[ValueT](ActionEvent):
    """The reader asked to open something: one of N entries, or one disclosure."""

    opened: ValueT


@dataclass(frozen=True, slots=True)
class Choices:
    """A picker over `choices`, backed by buttons or a select depending on shape.

    `minimum` defaults to 1, so the picker cannot be cleared to nothing without setting
    it to 0 explicitly; a small (2-5 choice) single-select (`maximum=1`) renders as
    buttons instead, which always select exactly one and ignore `minimum` entirely.
    """

    key: str
    choices: tuple[Choice, ...]
    selection: ChoiceOwnership = UNSELECTED
    minimum: int = 1
    maximum: int = 1
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Item:
    key: str
    label: TextLike
    children: tuple[LayoutNode, ...]
    summary: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Items:
    key: str
    items: tuple[Item, ...]
    opened: ItemOwnership = UNOPENED
    display: ItemDisplay = ItemDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Destination:
    key: str
    label: TextLike
    available: bool = True


@dataclass(frozen=True, slots=True)
class NavigateEvent(ActionEvent):
    destination: str = ""


@dataclass(frozen=True, slots=True)
class Navigation:
    key: str
    destinations: tuple[Destination, ...]
    current: NavOwnership = FIRST_DESTINATION
    """`None` means the first available destination."""
    display: NavigationDisplay = NavigationDisplay.AUTO
    flexibility: Flexibility = Flexibility.STABLE


@dataclass(frozen=True, slots=True)
class Truncated:
    node: LayoutNode
    keep: str = "head"


@dataclass(frozen=True, slots=True)
class Spilled:
    node: LayoutNode


@dataclass(frozen=True, slots=True)
class OptionalContent:
    node: LayoutNode
    importance: Importance = Importance.LOW


@dataclass(frozen=True, slots=True)
class FallbackContent:
    """Complete author-supplied representations of one region, best first."""

    primary: LayoutNode
    alternates: tuple[LayoutNode, ...]

    def __post_init__(self) -> None:
        if not self.alternates:
            message = "FallbackContent needs at least one alternate"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class BestEffort:
    node: LayoutNode


type SemanticNode = (
    Group
    | Stack
    | Cluster
    | Section
    | Article
    | Aside
    | Heading
    | Paragraph
    | Note
    | List
    | Fields
    | Table
    | Quote
    | Code
    | Figure
    | Media
    | Details
    | Status
    | Progress
    | Measure
    | Actions
    | Choices
    | Items
    | Navigation
)

type Adaptation = Truncated | Spilled | OptionalContent | FallbackContent | BestEffort
type LayoutNode = SemanticNode | Adaptation | PrimitiveNode


def truncate(node: LayoutNode, *, keep: str = "head") -> Truncated:
    """Allow prose in ``node`` to truncate when no lossless plan fits."""
    return Truncated(node, keep)


def spill(node: LayoutNode) -> Spilled:
    """Allow a static collection in ``node`` to omit its lowest-priority entries."""
    return Spilled(node)


def optional(node: LayoutNode, *, importance: Importance = Importance.LOW) -> OptionalContent:
    """Allow the whole node to disappear as an explicit last resort."""
    return OptionalContent(node, importance)


def fallback(primary: LayoutNode, *alternates: LayoutNode) -> FallbackContent:
    """Declare complete author-supplied alternate representations, in descending preference.

    Each alternate is a whole replacement for ``primary``, not a shortening of it; the planner
    steps down the ladder one rung at a time under component pressure.
    """
    if not alternates:
        message = "sl.fallback() needs at least one alternate"
        raise ValueError(message)
    return FallbackContent(primary, alternates)


def best_effort(node: LayoutNode) -> BestEffort:
    """Allow safe prose truncation and static collection spill, never consequential loss."""
    return BestEffort(node)
