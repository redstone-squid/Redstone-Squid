"""Frontend-neutral semantic layout vocabulary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from squid_layouts.actions import ActionEvent, ActionPolicy
from squid_layouts.ir import Node as PrimitiveNode
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
    FOCUSED = "focused"


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
    children: tuple[LayoutNode, ...]
    heading: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Article:
    children: tuple[LayoutNode, ...]
    heading: TextLike | None = None


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
    key: str
    label: TextLike
    value: TextLike
    importance: Importance = Importance.NORMAL


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
    open: bool = False


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
class ActionGroup:
    key: str
    actions: tuple[Action | Link, ...]
    label: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Actions:
    items: tuple[Action | Link | ActionGroup, ...]
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


@dataclass(frozen=True, slots=True)
class Choices:
    key: str
    choices: tuple[Choice, ...]
    selected: tuple[str, ...]
    on_change: Callable[[ChoiceEvent], Awaitable[None]]
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
    focused: str | None = None
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
    current: str
    on_navigate: Callable[[NavigateEvent], Awaitable[None]]
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
    primary: LayoutNode
    alternate: LayoutNode


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


def fallback(primary: LayoutNode, alternate: LayoutNode) -> FallbackContent:
    """Declare a complete author-supplied alternate representation."""
    return FallbackContent(primary, alternate)


def best_effort(node: LayoutNode) -> BestEffort:
    """Allow safe prose truncation and static collection spill, never consequential loss."""
    return BestEffort(node)
