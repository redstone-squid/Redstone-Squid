"""Describe what a UI contains before choosing how a target will display it.

Semantic nodes describe content and interaction: a Table contains rows and columns,
and Choices lets a reader select options. A target planner chooses a representation,
such as buttons or a select menu for Choices, subject to that target's limits.
The factories in squid_ui.factories are the usual way to construct these records.

Controlled and Uncontrolled specify who stores an interactive node's current value.
Adaptation wrappers specify which changes are acceptable when content does not fit:
for example, truncate permits shorter prose and optional permits omitting a region.
These declarations are frozen, but referenced application objects need not be.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Literal, overload

from squid_ui._dataclass import immutable
from squid_ui.authoring.grids import GridCell, validate_grid
from squid_ui.authoring.rosters import RosterPlacement
from squid_ui.boundary import Boundary
from squid_ui.content.assets import Asset
from squid_ui.content.entity import ConversationType, EntityRef, EntityType
from squid_ui.content.temporal import ZonedDateTime
from squid_ui.content.text import Text, TextLike, timestamp_text
from squid_ui.interactions import ActionEvent, ActionMode, BusySpec, PressHandler, SelectionEvent
from squid_ui.interactions.forms import FormSpec, SubmitHandler
from squid_ui.interactions.guards import Guard
from squid_ui.interactions.ownership import Controlled, Ownership, Uncontrolled
from squid_ui.native import NativeNode
from squid_ui.overflow import FIRST_PAGE, PageOwnership
from squid_ui.palette import INHERIT, Accent, Palette, Role, Tone
from squid_ui.target_types import Renderable, RenderTarget

if TYPE_CHECKING:
    from squid_ui.authoring.document import DocumentLike
    from squid_ui.layout import LayoutContext
    from squid_ui.runtime.histories import History


class ControlDisplay(StrEnum):
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
    MATRIX = "matrix"


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


class TimeStyle(StrEnum):
    SHORT_TIME = "t"
    LONG_TIME = "T"
    SHORT_DATE = "d"
    LONG_DATE = "D"
    SHORT_DATETIME = "f"
    FULL = "F"
    RELATIVE = "R"


# --- Who owns a node's value -----------------------------------------------------------


type ChoiceOwnership[ContextT = object] = Ownership[tuple[str, ...], ChoiceEvent[ContextT]]
type EntityOwnership[ContextT = object] = Ownership[tuple[EntityRef, ...], EntityEvent[ContextT]]
type ItemOwnership[ContextT = object] = Ownership[str | None, OpenEvent[str | None, ContextT]]
type DisclosureOwnership[ContextT = object] = Ownership[bool, OpenEvent[bool, ContextT]]
type ToggleOwnership[ContextT = object] = Ownership[bool, ToggleEvent[ContextT]]
type ScaleOwnership[ContextT = object] = Ownership[int | None, ScaleEvent[ContextT]]
type NavOwnership[ContextT = object] = Ownership[str | None, NavigateEvent[ContextT]]

# The engine-managed default of each stateful node, named for the state it seeds.
UNSELECTED: ChoiceOwnership = Uncontrolled(())
NO_ENTITIES: EntityOwnership = Uncontrolled(())
UNOPENED: ItemOwnership = Uncontrolled(None)
CLOSED: DisclosureOwnership = Uncontrolled(initial=False)
OFF: ToggleOwnership = Uncontrolled(initial=False)
UNRATED: ScaleOwnership = Uncontrolled(None)
FIRST_OPTION: NavOwnership = Uncontrolled(None)


# --- Nodes --------------------------------------------------------------------------------


class Adaptable[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    """A framework node that offers the adaptation wrappers as methods.

    Each method builds the same node as the function of the same name, so
    ``sl.paragraph(text).truncate()`` equals ``sl.truncate(sl.paragraph(text))``. Chained calls
    apply in reading order: ``node.truncate().budget(min=100, prefer=350)`` budgets the
    truncated node. Around a multi-line subtree, prefer the function: a trailing method would
    put the decision about how the region may shrink after everything it governs.

    Semantic nodes and the adaptation wrappers inherit this; a custom ``Renderable`` does not,
    so these names stay free for its own fields. ``fallback`` has no method because its
    candidates are peers rather than one node and its modifiers.
    """

    __slots__ = ()

    def truncate(self, *, keep: Literal["head", "tail"] = "head") -> Truncated[RenderTargetT, ContextT]:
        """Same as ``truncate(self, keep=keep)``."""
        return truncate(self, keep=keep)

    def spill(self) -> Spilled[RenderTargetT, ContextT]:
        """Same as ``spill(self)``."""
        return spill(self)

    def optional(self, *, importance: Importance = Importance.LOW) -> OptionalContent[RenderTargetT, ContextT]:
        """Same as ``optional(self, importance=importance)``."""
        return optional(self, importance=importance)

    def best_effort(self) -> BestEffort[RenderTargetT, ContextT]:
        """Same as ``best_effort(self)``."""
        return best_effort(self)

    def budget(self, *, min: int, prefer: int, stretch: int = 0) -> Budgeted[RenderTargetT, ContextT]:
        """Same as ``budget(self, min=min, prefer=prefer, stretch=stretch)``."""
        return budget(self, min=min, prefer=prefer, stretch=stretch)

    def unbreakable(self) -> Unbreakable[RenderTargetT, ContextT]:
        """Same as ``unbreakable(self)``."""
        return unbreakable(self)

    def keep_with_next(self) -> KeepWithNext[RenderTargetT, ContextT]:
        """Same as ``keep_with_next(self)``."""
        return keep_with_next(self)

    def paged(
        self,
        *,
        key: str,
        chars: int,
        min: int = 0,
        stretch: int = 0,
        min_fill: int = 0,
        widows: int = 1,
        page: PageOwnership = FIRST_PAGE,
        footer: Callable[[LayoutContext], TextLike] | None = None,
    ) -> Budgeted[RenderTargetT, ContextT]:
        """Same as ``paged(self, ...)`` with the same keyword arguments."""
        return paged(
            self,
            key=key,
            chars=chars,
            min=min,
            stretch=stretch,
            min_fill=min_fill,
            widows=widows,
            page=page,
            footer=footer,
        )


@immutable
class Group[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]

    key: str | None = None


@immutable
class Contextual[RenderTargetT = RenderTarget, ContextT = object](Group[RenderTargetT, ContextT]):
    """Pure contextual content with component placements declared before planning."""

    callback: Callable[[LayoutContext], DocumentLike[RenderTargetT, ContextT]] = field(kw_only=True)
    components: tuple[Boundary[RenderTargetT, ContextT], ...] = field(default=(), kw_only=True)
    _prepared: tuple[Group[RenderTargetT, ContextT], ...] = field(default=(), kw_only=True, repr=False)
    _scope: str = field(default="$", kw_only=True, repr=False)
    _identity: str = field(default="", kw_only=True, repr=False)


@immutable
class ComponentPlacement[RenderTargetT = RenderTarget, ContextT = object](Group[RenderTargetT, ContextT]):
    """A component boundary retained for source queries and delivered visibility."""

    placement: str = field(default="", kw_only=True)


@immutable
class PreparedSlot[RenderTargetT = RenderTarget, ContextT = object](ComponentPlacement[RenderTargetT, ContextT]):
    """A declared subtree whose action keys already include its placement."""


@immutable
class Stack[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]

    key: str | None = None


@immutable
class Cluster[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]


@immutable
class Themed[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """A subtree planned with a presentation palette override."""

    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    palette: Palette


@immutable
class Block[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """An untitled semantic region with a palette role, exact colour, or inherited accent."""

    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    accent: Accent = INHERIT

    key: str | None = None


@immutable
class Section[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """A titled block of related content.

    ``accent`` accepts a `Tone`, custom `Role`, or `AccentRole.BRAND`, resolved through the active palette.
    Omit it to use `Palette.brand`, pass ``None`` for no accent, or pass an exact colour
    when the value is data, such as a guild's configured colour.

    ``thumbnail`` is a lead image shown beside the required heading.
    """

    heading: Heading
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    accent: Accent = INHERIT
    thumbnail: str | None = None

    key: str | None = None


@immutable
class Article[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """A self-contained block that stands on its own; see `Section` for the extras."""

    heading: Heading
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    accent: Accent = INHERIT
    thumbnail: str | None = None


@immutable
class Aside[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    tone: Tone | Role = Tone.NEUTRAL


@immutable
class Heading(Adaptable[RenderTarget]):
    content: TextLike
    level: int = 2
    importance: Importance = Importance.HIGH


@immutable
class Paragraph(Adaptable[RenderTarget]):
    content: TextLike
    importance: Importance = Importance.NORMAL

    key: str | None = None


@immutable
class ListItem:
    key: str
    content: TextLike
    importance: Importance = Importance.NORMAL


@immutable
class List(Adaptable[RenderTarget]):
    """A keyed list with application-owned or remembered paging.

    page_size limits item count but allows further splits to fit. group_size
    fixes consecutive groups: each group must fit using permitted adaptations
    or planning fails. Ordered numbering spans the full list.
    """

    items: tuple[ListItem, ...]
    key: str
    ordered: bool = False
    page_size: int | None = None
    page: PageOwnership = FIRST_PAGE
    markers: bool = field(default=True, kw_only=True)
    group_size: int | None = field(default=None, kw_only=True)

    @property
    def group_count(self) -> int | None:
        """Count authored groups, without claiming that they fit the target."""
        return (len(self.items) + self.group_size - 1) // self.group_size if self.group_size is not None else None

    def __post_init__(self) -> None:
        if self.group_size is not None and self.group_size < 1:
            message = "List.group_size must be positive"
            raise ValueError(message)
        if self.group_size is not None and self.page_size is not None:
            message = "choose group_size for fixed groups or page_size for a maximum"
            raise ValueError(message)
        if self.page_size is not None and self.page_size < 1:
            message = "List.page_size must be positive"
            raise ValueError(message)


@immutable
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


@immutable
class Fields(Adaptable[RenderTarget]):
    fields: tuple[Field, ...]


@immutable
class Column:
    key: str
    heading: TextLike


@immutable
class Columns:
    columns: tuple[Column, ...]


@immutable
class TableRow:
    key: str
    cells: tuple[TextLike, ...]


@immutable
class Table(Adaptable[RenderTarget]):
    columns: Columns
    rows: tuple[TableRow, ...]
    key: str
    display: TableDisplay = TableDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL

    def __post_init__(self) -> None:
        if not self.columns.columns:
            message = "Table needs at least one column"
            raise ValueError(message)
        width = len(self.columns.columns)
        if row := next((row for row in self.rows if len(row.cells) != width), None):
            message = f"Table row {row.key!r} has {len(row.cells)} cells for {width} columns"
            raise ValueError(message)


@immutable
class Note(Adaptable[RenderTarget]):
    """Small print: an id, a timestamp, a caveat the reader may skip."""

    content: TextLike
    importance: Importance = Importance.LOW


@immutable
class Quote(Adaptable[RenderTarget]):
    content: TextLike
    attribution: TextLike | None = None


@immutable
class Code(Adaptable[RenderTarget]):
    content: str
    language: str = ""


@immutable
class MediaItem:
    key: str
    source: str | Asset
    description: TextLike | None = None
    spoiler: bool = False


@immutable
class Figure(Adaptable[RenderTarget]):
    media: MediaItem
    caption: TextLike | None = None


@immutable
class Media(Adaptable[RenderTarget]):
    items: tuple[MediaItem, ...]
    key: str
    display: MediaDisplay = MediaDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@immutable
class Summary:
    content: TextLike


@immutable
class Details[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    key: str
    summary: Summary
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    open: DisclosureOwnership[ContextT] = CLOSED


@immutable
class ToggleEvent[ContextT = object](ActionEvent[ContextT]):
    """The reader requested a new boolean value."""

    value: bool = False


@immutable
class Toggle[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """One keyed boolean control with explicit state ownership."""

    key: str
    label: TextLike
    on: ToggleOwnership[ContextT] = OFF
    on_label: TextLike | None = None
    off_label: TextLike | None = None
    tone: Tone | Role = Tone.NEUTRAL
    available: bool = True


@immutable
class Download(Adaptable[RenderTarget]):
    """A visible file control whose asset is declared at its point of use."""

    key: str
    label: TextLike | None
    asset: Asset
    description: TextLike | None = None
    emphasis: Emphasis = Emphasis.NORMAL
    spoiler: bool = False


@immutable
class Status(Adaptable[RenderTarget]):
    content: TextLike
    tone: Tone | Role = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL


@immutable
class ProgressBar(Adaptable[RenderTarget]):
    value: float
    label: TextLike | None = None
    maximum: float = 1.0


@immutable
class Roster[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """A host-owned roster allocation rendered with active localized chrome."""

    key: str
    placement: RosterPlacement
    on_join: Callable[[SelectionEvent[ContextT]], Awaitable[None]] | None = None
    routes: Mapping[str, str] | None = None
    locked: bool = False
    show_waitlist: bool = True

    def __post_init__(self) -> None:
        if not self.key:
            message = "Roster key must not be empty"
            raise ValueError(message)
        if self.on_join is not None and self.routes is not None:
            message = "Roster takes on_join or routes, not both"
            raise ValueError(message)
        slot_keys = {group.slot.key for group in self.placement.groups}
        if self.routes is not None and set(self.routes) != slot_keys:
            message = "Roster routes must contain exactly one route for every slot"
            raise ValueError(message)


@immutable
class Grid[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """A selectable spatial collection with target-shaped fallback strategies."""

    key: str
    cells: tuple[GridCell, ...]
    columns: int
    on_pick: Callable[[SelectionEvent[ContextT]], Awaitable[None]]
    flexibility: Flexibility = Flexibility.NORMAL

    def __post_init__(self) -> None:
        if not self.key:
            message = "Grid key must not be empty"
            raise ValueError(message)
        validate_grid(self.cells, self.columns)


@immutable
class Metric(Adaptable[RenderTarget]):
    value: int | float | str
    label: TextLike
    unit: str | None = None


@immutable
class Timestamp(Adaptable[RenderTarget]):
    """An aware instant plus a portable display preference."""

    instant: datetime
    style: TimeStyle = TimeStyle.SHORT_DATETIME
    label: TextLike | None = None

    def as_text(self) -> Text:
        return timestamp_text(self.instant, style=self.style.value)


@immutable
class ZonedTimestamp(Adaptable[RenderTarget]):
    """An exact instant rendered visibly in its named timezone."""

    value: ZonedDateTime
    label: TextLike | None = None


@immutable
class FormTrigger[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """A content entry point that presents a portable form."""

    key: str
    label: TextLike
    spec: FormSpec[ContextT]
    on_submit: SubmitHandler[ContextT]
    mode: ActionMode = ActionMode.EXCLUSIVE
    tone: Tone | Role = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    guard: Guard | None = None
    """Admission for the press that opens the form; the submission is not re-admitted."""
    record: History | None = None
    """History the successful submission enters under this trigger's label."""


@immutable
class ActionControl[ContextT = object]:
    key: str
    label: TextLike
    on_trigger: PressHandler[ContextT]
    tone: Tone | Role = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True
    allow_grouping: bool | None = None
    mode: ActionMode = ActionMode.EXCLUSIVE
    guard: Guard | None = None
    """Whether this press may execute now. `available` is the render-time question."""
    busy: BusySpec | None = None
    """Interim presentation requested while a slow handler runs."""
    record: History | None = None
    """History this press enters itself into, under `label`, before `on_trigger` runs."""


@immutable
class Link:
    key: str
    label: TextLike
    url: str
    emphasis: Emphasis = Emphasis.NORMAL


@immutable
class RoutedActionControl:
    """A control dispatched by a host route rather than an in-memory mounted handler.

    For example, a button on a posted card can keep working after a process restart
    because its route ID identifies the operation without retaining a Python callback.
    Use Route.id to validate the ID format and length. The host must keep the route
    registered and authorize each request; mounted action lifecycle policies do not
    follow automatically from using a route.
    """

    key: str
    label: TextLike
    route_id: str
    tone: Tone | Role = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True


@immutable
class ControlGroup[ContextT = object]:
    key: str
    controls: tuple[ActionControl[ContextT] | Link | RoutedActionControl, ...]
    label: TextLike | None = None


@immutable
class ActionControls[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """A set of controls that the planner lays out together.

    Shown as buttons, the controls keep their order and share rows, so a toggle or form can sit
    beside the actions it belongs with. Each `ControlGroup` starts on a new row. When space is
    short, the planner may fold plain actions into a select menu; links, routed controls,
    toggles, and forms always stay buttons.
    """

    items: tuple[
        ActionControl[ContextT]
        | Link
        | RoutedActionControl
        | Toggle[ContextT]
        | FormTrigger[ContextT]
        | ControlGroup[ContextT],
        ...,
    ]
    key: str
    display: ControlDisplay = ControlDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@immutable
class Choice:
    key: str
    label: TextLike
    description: TextLike | None = None
    available: bool = True


@immutable
class ChoiceEvent[ContextT = object](ActionEvent[ContextT]):
    selected: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@immutable
class EntityEvent[ContextT = object](ActionEvent[ContextT]):
    selected: tuple[EntityRef, ...] = ()
    added: tuple[EntityRef, ...] = ()
    removed: tuple[EntityRef, ...] = ()


@immutable
class EntityChoice:
    ref: EntityRef
    label: TextLike
    description: TextLike | None = None
    available: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenEvent[ValueT, ContextT = object](ActionEvent[ContextT]):
    """The reader asked to open something: one of N entries, or one disclosure."""

    opened: ValueT


@immutable
class ScaleEvent[ContextT = object](ActionEvent[ContextT]):
    """The reader picked one point on an ordinal scale."""

    value: int = 0


@immutable
class Choices[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """Let the reader select between minimum and maximum options.

    The selection uses Controlled or Uncontrolled to decide who stores the selected
    option keys. Minimum zero permits an empty selection where the target supports
    clearing. Discord may render a small single-select picker as buttons, whose presses
    always select one option. Other targets choose their own representation.
    """

    key: str
    choices: tuple[Choice, ...]
    selection: ChoiceOwnership[ContextT] = UNSELECTED
    minimum: int = 1
    maximum: int = 1
    flexibility: Flexibility = Flexibility.NORMAL


@immutable
class Entities[ContextT = object](Adaptable[RenderTarget, ContextT]):
    """A picker over frontend-resolved entities with an optional enumerated fallback."""

    key: str
    entity_type: EntityType
    choices: tuple[EntityChoice, ...] = ()
    selection: EntityOwnership[ContextT] = NO_ENTITIES
    minimum: int = 1
    maximum: int = 1
    conversation_types: tuple[ConversationType, ...] = ()
    placeholder: TextLike | None = None
    flexibility: Flexibility = Flexibility.NORMAL

    def __post_init__(self) -> None:
        if self.conversation_types and self.entity_type is not EntityType.CONVERSATION:
            message = "conversation_types is only valid for conversation entity pickers"
            raise ValueError(message)
        if any(not self.entity_type.supports(choice.ref.kind) for choice in self.choices):
            message = f"fallback choice is incompatible with {self.entity_type.value} entity picker"
            raise ValueError(message)


@immutable
class RoutedChoices(Adaptable[RenderTarget]):
    """An explicitly stateless picker whose values are submitted to one route."""

    key: str
    choices: tuple[Choice, ...]
    route_id: str
    placeholder: TextLike | None = None
    minimum: int = 1
    maximum: int = 1
    available: bool = True
    selected: tuple[str, ...] = ()


@immutable
class ItemLabel:
    content: TextLike


@immutable
class Item[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    key: str
    label: ItemLabel
    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    summary: TextLike | None = None


@immutable
class Items[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    key: str
    items: tuple[Item[RenderTargetT, ContextT], ...]
    opened: ItemOwnership[ContextT] = UNOPENED
    display: ItemDisplay = ItemDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@immutable
class NavOption:
    key: str
    label: TextLike
    available: bool = True


@immutable
class NavigateEvent[ContextT = object](ActionEvent[ContextT]):
    destination: str = ""


@immutable
class Navigation[ContextT = object](Adaptable[RenderTarget, ContextT]):
    key: str
    options: tuple[NavOption, ...]
    current: NavOwnership[ContextT] = FIRST_OPTION
    """`None` means the first available destination."""
    display: NavigationDisplay = NavigationDisplay.AUTO
    flexibility: Flexibility = Flexibility.STABLE


@immutable
class Truncated[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    node: LayoutNode[RenderTargetT, ContextT]
    keep: Literal["head", "tail"] = "head"


@immutable
class Spilled[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    node: LayoutNode[RenderTargetT, ContextT]


@immutable
class OptionalContent[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    node: LayoutNode[RenderTargetT, ContextT]
    importance: Importance = Importance.LOW


@immutable
class FallbackContent[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """Complete author-supplied representations of one region, best first."""

    primary: LayoutNode[RenderTargetT, ContextT]
    alternates: tuple[LayoutNode[RenderTargetT, ContextT], ...]

    def __post_init__(self) -> None:
        if not self.alternates:
            message = "FallbackContent needs at least one alternate"
            raise ValueError(message)


@immutable
class BestEffort[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    node: LayoutNode[RenderTargetT, ContextT]


@immutable
class Budgeted[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """Reserve and cap the character grant for one logical region."""

    node: LayoutNode[RenderTargetT, ContextT]
    minimum: int
    preferred: int
    stretch: int = 0

    def __post_init__(self) -> None:
        if self.minimum < 0 or self.preferred < 0 or self.stretch < 0:
            message = "layout budgets must not be negative"
            raise ValueError(message)
        if self.minimum > self.preferred:
            message = "layout budget min must not exceed prefer"
            raise ValueError(message)


@immutable
class Unbreakable[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """Keep every primitive produced by ``node`` together on a region page."""

    node: LayoutNode[RenderTargetT, ContextT]


@immutable
class KeepWithNext[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """Forbid a region page break immediately after ``node``."""

    node: LayoutNode[RenderTargetT, ContextT]


@immutable
class Region[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """A jointly allocated page region with persistent content and independent position.

    Headers and footers repeat within this region. Nested regions retain their own
    positions; their maximum page costs count against the containing region.
    """

    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    key: str
    title: TextLike
    header: tuple[LayoutNode[RenderTargetT, ContextT], ...] = ()
    footer: tuple[LayoutNode[RenderTargetT, ContextT], ...] = ()
    page: PageOwnership = FIRST_PAGE
    page_size: int | None = None

    def __post_init__(self) -> None:
        if not self.key:
            msg = "region key must not be empty"
            raise ValueError(msg)
        if self.page_size is not None and self.page_size < 1:
            message = "region page_size must be positive"
            raise ValueError(message)


@immutable
class Paged[RenderTargetT = RenderTarget, ContextT = object](Adaptable[RenderTargetT, ContextT]):
    """Paginate a keyed region's children, resolving `page` after measuring their sizes."""

    node: LayoutNode[RenderTargetT, ContextT]
    key: str
    chars: int
    min_fill: int = 0
    widows: int = 1
    page: PageOwnership = FIRST_PAGE
    footer: Callable[[LayoutContext], TextLike] | None = None

    def __post_init__(self) -> None:
        if not self.key:
            message = "paged region key must not be empty"
            raise ValueError(message)
        if self.chars < 1:
            message = "paged region chars must be positive"
            raise ValueError(message)
        if self.min_fill < 0:
            message = "paged region min_fill must not be negative"
            raise ValueError(message)
        if self.widows < 1:
            message = "paged region widows must be at least 1"
            raise ValueError(message)


type SemanticNode[RenderTargetT = RenderTarget, ContextT = object] = (
    Group[RenderTargetT, ContextT]
    | Stack[RenderTargetT, ContextT]
    | Cluster[RenderTargetT, ContextT]
    | Themed[RenderTargetT, ContextT]
    | Block[RenderTargetT, ContextT]
    | Section[RenderTargetT, ContextT]
    | Article[RenderTargetT, ContextT]
    | Aside[RenderTargetT, ContextT]
    | Details[RenderTargetT, ContextT]
    | Items[RenderTargetT, ContextT]
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
    | Toggle[ContextT]
    | Download
    | Status
    | ProgressBar
    | Roster[ContextT]
    | Grid[ContextT]
    | Metric
    | Timestamp
    | ZonedTimestamp
    | FormTrigger[ContextT]
    | ActionControls[ContextT]
    | Choices[ContextT]
    | Entities[ContextT]
    | RoutedChoices
    | Navigation[ContextT]
)
"""The framework's semantic node types, each describing content rather than target widgets.

Leaves such as Table are portable. Containers retain any target restrictions imposed
by their children, so a stack containing a Discord-only primitive is also Discord-only.
"""

type Adaptation[RenderTargetT = RenderTarget, ContextT = object] = (
    Truncated[RenderTargetT, ContextT]
    | Spilled[RenderTargetT, ContextT]
    | OptionalContent[RenderTargetT, ContextT]
    | BestEffort[RenderTargetT, ContextT]
    | Budgeted[RenderTargetT, ContextT]
    | Unbreakable[RenderTargetT, ContextT]
    | KeepWithNext[RenderTargetT, ContextT]
    | Paged[RenderTargetT, ContextT]
    | Region[RenderTargetT, ContextT]
)
type AnyLayoutNode = LayoutNode[Any, Any]
"""A node whose dialect is deliberately not tracked.

What the planner, the tree rewriters and the runtime walk. They rewrite whatever they are
handed and leave the dialect judgement to the target's dialect, so narrowing them to the
portable default would be a claim none of them makes.
"""

type PortableNode[RenderTargetT = RenderTarget, ContextT = object] = (
    SemanticNode[RenderTargetT, ContextT]
    | Adaptation[RenderTargetT, ContextT]
    | FallbackContent[RenderTargetT, ContextT]
)
"""The closed portable vocabulary: what every planner backend must answer for.

`LayoutNode` minus the open `Renderable` escape. A traversal that matches over this union
can be proven exhaustive, which `AnyLayoutNode` structurally cannot.
"""
type BuiltinLayoutNode = SemanticNode | Adaptation | FallbackContent | NativeNode[Any, Any] | Boundary[Any, Any]
"""Every layout node the framework itself ships, as classes an `isinstance` can test.

The closed spelling of `LayoutNode`, which is open at its `Renderable` arm. "Builtin" is
the operative word: a caller's own `Renderable` is a layout node and is deliberately not
one of these, which is what the Discord lowering needs to say when it meets one.
"""
type LayoutNode[RenderTargetT = RenderTarget, ContextT = object] = Renderable[RenderTargetT, ContextT]
"""An authored node, including custom Renderable subclasses.

Renderable is the capability a class declares by inheritance; LayoutNode names the
same type when referring to its place in the authored layout tree.
This open type does not support exhaustive matching. Use PortableNode for the closed
portable vocabulary or BuiltinLayoutNode for all framework-defined nodes.
"""


def truncate[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT], *, keep: Literal["head", "tail"] = "head"
) -> Truncated[RenderTargetT, ContextT]:
    """Allow prose in ``node`` to truncate when no lossless plan fits."""
    return Truncated(node, keep)


def spill[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
) -> Spilled[RenderTargetT, ContextT]:
    """Allow a static collection in ``node`` to omit its lowest-priority entries."""
    return Spilled(node)


def optional[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT], *, importance: Importance = Importance.LOW
) -> OptionalContent[RenderTargetT, ContextT]:
    """Allow the whole node to disappear as an explicit last resort."""
    return OptionalContent(node, importance)


# Positional-only throughout. The arity ladder exists to union each rung's render target into the
# result, which a `*args` signature cannot express; but the implementation *is* variadic, so
# an overload naming its parameters would promise a keyword call the implementation cannot
# accept, and the two signatures would not agree.
@overload
def fallback[FirstT, SecondT](
    primary: LayoutNode[FirstT], alternate: LayoutNode[SecondT], /
) -> FallbackContent[FirstT | SecondT]: ...


@overload
def fallback[FirstT, SecondT, ThirdT](
    primary: LayoutNode[FirstT], first: LayoutNode[SecondT], second: LayoutNode[ThirdT], /
) -> FallbackContent[FirstT | SecondT | ThirdT]: ...


@overload
def fallback[FirstT, SecondT, ThirdT, FourthT](
    primary: LayoutNode[FirstT],
    first: LayoutNode[SecondT],
    second: LayoutNode[ThirdT],
    third: LayoutNode[FourthT],
    /,
) -> FallbackContent[FirstT | SecondT | ThirdT | FourthT]: ...


@overload
def fallback[FirstT, SecondT, ThirdT, FourthT, FifthT](
    primary: LayoutNode[FirstT],
    first: LayoutNode[SecondT],
    second: LayoutNode[ThirdT],
    third: LayoutNode[FourthT],
    fourth: LayoutNode[FifthT],
    /,
) -> FallbackContent[FirstT | SecondT | ThirdT | FourthT | FifthT]: ...


@overload
def fallback(primary: AnyLayoutNode, /, *alternates: AnyLayoutNode) -> FallbackContent[Any]: ...


def fallback(primary: AnyLayoutNode, /, *alternates: AnyLayoutNode) -> FallbackContent[Any]:
    """Declare complete author-supplied alternate representations, in descending preference.

    Each alternate is a whole replacement for ``primary``, not a shortening of it; the planner
    steps down the ladder one rung at a time under component pressure.
    Shared planning reports replacements as fidelity loss. Use checked ``alternatives``
    to declare equivalence when strict mode must permit a different representation.
    """
    if not alternates:
        message = "sl.fallback() needs at least one alternate"
        raise ValueError(message)
    return FallbackContent(primary, alternates)


def best_effort[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
) -> BestEffort[RenderTargetT, ContextT]:
    """Allow safe prose truncation and static collection spill, never consequential loss."""
    return BestEffort(node)


def budget[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT], *, min: int, prefer: int, stretch: int = 0
) -> Budgeted[RenderTargetT, ContextT]:
    """Give ``node`` a hard floor, preferred size, and lossless stretch band."""
    return Budgeted(node, min, prefer, stretch)


def unbreakable[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
) -> Unbreakable[RenderTargetT, ContextT]:
    """Keep ``node`` atomic when its containing region paginates."""
    return Unbreakable(node)


def keep_with_next[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
) -> KeepWithNext[RenderTargetT, ContextT]:
    """Keep ``node`` off the bottom of a region page without its successor."""
    return KeepWithNext(node)


def paged[RenderTargetT = RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
    *,
    key: str,
    chars: int,
    min: int = 0,
    stretch: int = 0,
    min_fill: int = 0,
    widows: int = 1,
    page: PageOwnership = FIRST_PAGE,
    footer: Callable[[LayoutContext], TextLike] | None = None,
) -> Budgeted[RenderTargetT, ContextT]:
    """Apply a preferred character budget and heterogeneous paging to ``node``."""
    region = Paged(node, key, chars, min_fill, widows, page, footer)
    return Budgeted(region, min, chars, stretch)


__all__ = [
    "CLOSED",
    "FIRST_OPTION",
    "NO_ENTITIES",
    "OFF",
    "UNOPENED",
    "UNRATED",
    "UNSELECTED",
    "ActionControl",
    "ActionControls",
    "Adaptable",
    "Adaptation",
    "AnyLayoutNode",
    "Article",
    "Aside",
    "BestEffort",
    "Block",
    "Budgeted",
    "Choice",
    "ChoiceEvent",
    "ChoiceOwnership",
    "Choices",
    "Cluster",
    "Code",
    "Column",
    "Columns",
    "Contextual",
    "ControlDisplay",
    "ControlGroup",
    "Controlled",
    "DetailLevel",
    "Details",
    "DisclosureOwnership",
    "Download",
    "Emphasis",
    "Entities",
    "EntityChoice",
    "EntityEvent",
    "EntityOwnership",
    "FallbackContent",
    "Field",
    "Fields",
    "Figure",
    "Flexibility",
    "FormTrigger",
    "Group",
    "Heading",
    "Importance",
    "Item",
    "ItemDisplay",
    "ItemLabel",
    "ItemOwnership",
    "Items",
    "KeepWithNext",
    "LayoutNode",
    "Link",
    "List",
    "ListItem",
    "Media",
    "MediaDisplay",
    "MediaItem",
    "Metric",
    "NavOption",
    "NavOwnership",
    "NavigateEvent",
    "Navigation",
    "NavigationDisplay",
    "Note",
    "OpenEvent",
    "OptionalContent",
    "Ownership",
    "Paged",
    "Paragraph",
    "PortableNode",
    "ProgressBar",
    "Quote",
    "Region",
    "Roster",
    "RoutedActionControl",
    "RoutedChoices",
    "ScaleEvent",
    "ScaleOwnership",
    "Section",
    "SemanticNode",
    "Spilled",
    "Stack",
    "Status",
    "Summary",
    "Table",
    "TableDisplay",
    "TableRow",
    "Themed",
    "TimeStyle",
    "Timestamp",
    "Toggle",
    "ToggleEvent",
    "ToggleOwnership",
    "Tone",
    "Truncated",
    "Unbreakable",
    "Uncontrolled",
    "ZonedTimestamp",
    "best_effort",
    "budget",
    "fallback",
    "keep_with_next",
    "optional",
    "paged",
    "spill",
    "truncate",
    "unbreakable",
]
