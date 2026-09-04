"""Frontend-neutral semantic layout vocabulary."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Literal, overload

from squid_ui.assets import Asset
from squid_ui.entity import ConversationType, EntityRef, EntityType, supports_entity
from squid_ui.forms import FormSpec, SubmitHandler
from squid_ui.grids import GridCell, validate_grid
from squid_ui.guards import Guard
from squid_ui.interactions import ActionEvent, ActionMode, BusySpec, PressHandler, SelectionEvent
from squid_ui.palette import INHERIT, Accent, Palette, Tone
from squid_ui.primitives.nodes import Node
from squid_ui.rosters import RosterPlacement
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


class ControlDisplay(StrEnum):
    """The preferred `ActionControls` strategy; the planner drops it when the controls do not fit.

    `INDIVIDUAL` is one button per action, `GROUPED` folds each group's eligible actions into a
    select of 25, and `AUTO` is individual up to five actions.
    """

    AUTO = "auto"
    INDIVIDUAL = "individual"
    GROUPED = "grouped"


class NavigationDisplay(StrEnum):
    """The preferred `Navigation` strategy: `INDIVIDUAL` buttons, a `GROUPED` select, or `AUTO` — buttons up to five."""

    AUTO = "auto"
    INDIVIDUAL = "individual"
    GROUPED = "grouped"


class ItemDisplay(StrEnum):
    """Which `Items` view to prefer; `OPENED` opens the first item when the session holds none."""

    AUTO = "auto"
    OVERVIEW = "overview"
    OPENED = "opened"


class TableDisplay(StrEnum):
    """How a `Table` is drawn.

    `TABULAR` and `MATRIX` are a code block, `|`-separated with a rule and space-aligned
    respectively; `RECORDS` is one `**heading:** cell` paragraph per row, paginated. `AUTO`
    is tabular up to four columns and records above, and never picks matrix.
    """

    AUTO = "auto"
    TABULAR = "tabular"
    RECORDS = "records"
    MATRIX = "matrix"


class DetailLevel(StrEnum):
    """Not yet read by any planner."""

    AUTO = "auto"
    FULL = "full"
    SUMMARY = "summary"


class MediaDisplay(StrEnum):
    """`FEATURED` shows only the first item, `COLLECTION` every item in galleries; `AUTO` is collection."""

    AUTO = "auto"
    COLLECTION = "collection"
    FEATURED = "featured"


class Flexibility(IntEnum):
    """How dearly a node's strategy may change between renders; read by the cost model only.

    Each tier is its own `CostVector` column, `STABLE` outranking `NORMAL` outranking
    `FLEXIBLE`, so a stable node keeps last render's strategy before any normal node does.
    It never changes which strategies are available.
    """

    FLEXIBLE = 0
    NORMAL = 1
    STABLE = 2


class Importance(IntEnum):
    """Planner priority when trimming: lower-importance content is dropped or shortened first."""

    LOW = -100
    NORMAL = 0
    HIGH = 100


class Emphasis(StrEnum):
    """Visual weight, read in two places only.

    A button is primary for `STRONG` and secondary otherwise, unless its `Tone` decides; a
    `Download` label is bold for `STRONG` and small print for `SUBTLE`. `Status` and `Link`
    ignore it.
    """

    SUBTLE = "subtle"
    NORMAL = "normal"
    STRONG = "strong"


class TimeStyle(StrEnum):
    """Discord's `<t:unix:X>` style letter; other targets map each to their nearest format."""

    SHORT_TIME = "t"
    LONG_TIME = "T"
    SHORT_DATE = "d"
    LONG_DATE = "D"
    SHORT_DATETIME = "f"
    FULL = "F"
    RELATIVE = "R"


# --- Who owns a node's value -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Controlled[ValueT, EventT]:
    """The author owns this value: authoritative on every render, never written by the engine."""

    value: ValueT
    on_change: Callable[[EventT], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Uncontrolled[ValueT]:
    """The engine owns this value, in the presentation session under the node's key.

    ``initial`` is a seed, not a value: it applies on a session miss and is ignored from
    then on. An author who needs their value to keep winning wants `Controlled`.
    """

    initial: ValueT


type Ownership[ValueT, EventT] = Controlled[ValueT, EventT] | Uncontrolled[ValueT]
"""Every stateful semantic node takes one of these, and it is the whole ownership story.

Ownership is a value rather than something inferred from whether a handler was passed,
so a node cannot be half-controlled and the mode is readable at the call site.
"""


type ChoiceOwnership = Ownership[tuple[str, ...], ChoiceEvent]
type EntityOwnership = Ownership[tuple[EntityRef, ...], EntityEvent]
type ItemOwnership = Ownership[str | None, OpenEvent[str | None]]
type DisclosureOwnership = Ownership[bool, OpenEvent[bool]]
type ToggleOwnership = Ownership[bool, ToggleEvent]
type ScaleOwnership = Ownership[int | None, ScaleEvent]
type NavOwnership = Ownership[str | None, NavigateEvent]

# The engine-managed default of each stateful node, named for the state it seeds.
UNSELECTED: ChoiceOwnership = Uncontrolled(())
NO_ENTITIES: EntityOwnership = Uncontrolled(())
UNOPENED: ItemOwnership = Uncontrolled(None)
CLOSED: DisclosureOwnership = Uncontrolled(initial=False)
OFF: ToggleOwnership = Uncontrolled(initial=False)
UNRATED: ScaleOwnership = Uncontrolled(None)
FIRST_OPTION: NavOwnership = Uncontrolled(None)


@dataclass(frozen=True, slots=True)
class Group[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Related content with no layout opinion. Discord lowers it to its children in place; HTML to a `div`."""

    children: tuple[LayoutNode[RenderTargetT], ...]


@dataclass(frozen=True, slots=True)
class Stack[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Children read top to bottom. Discord lowers it to its children in place; HTML to a `div`."""

    children: tuple[LayoutNode[RenderTargetT], ...]


@dataclass(frozen=True, slots=True)
class Cluster[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Children read as a set. Discord lowers it to its children in place; HTML to a `div`."""

    children: tuple[LayoutNode[RenderTargetT], ...]


@dataclass(frozen=True, slots=True)
class Themed[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """A subtree whose accents and tones resolve through `palette` instead of the active one."""

    children: tuple[LayoutNode[RenderTargetT], ...]
    palette: Palette


@dataclass(frozen=True, slots=True)
class Block[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """An untitled region: an embed on classic targets, a container on Components V2.

    `accent` is the embed colour or container accent; nested inside another region it
    lowers to its children alone.
    """

    children: tuple[LayoutNode[RenderTargetT], ...]
    accent: Accent = INHERIT


@dataclass(frozen=True, slots=True)
class Section[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """A titled region: one embed on classic targets, a container on Components V2.

    `heading` is the embed title or the container's leading heading, and `thumbnail` the
    image beside it.

    ``accent`` is an exact colour override, not a semantic fact. Omit it to inherit the
    active `Palette.brand`, pass ``None`` to opt out of an inherited accent, and pass a
    colour when the exact value is data (such as a guild's configured colour). Colour that
    *means* something — advisory, warning, failed — belongs on `Aside` or `Status` via
    `Tone`, which the active palette maps without discarding the semantic meaning.
    """

    heading: Heading
    children: tuple[LayoutNode[RenderTargetT], ...]
    accent: Accent = INHERIT
    thumbnail: str | None = None


@dataclass(frozen=True, slots=True)
class Article[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """A self-contained `Section`; the Discord planner lowers both identically."""

    heading: Heading
    children: tuple[LayoutNode[RenderTargetT], ...]
    accent: Accent = INHERIT
    thumbnail: str | None = None


@dataclass(frozen=True, slots=True)
class Aside[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """An untitled region whose accent is the palette's colour for `tone`, not an author override."""

    children: tuple[LayoutNode[RenderTargetT], ...]
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class Heading(Renderable[RenderTarget]):
    """A heading that never truncates; `level` is the Markdown `#` depth on Components V2."""

    content: TextLike
    level: int = 2
    importance: Importance = Importance.HIGH


@dataclass(frozen=True, slots=True)
class Paragraph(Renderable[RenderTarget]):
    """Prose that never truncates unless wrapped in `truncate`/`best_effort`."""

    content: TextLike
    importance: Importance = Importance.NORMAL


@dataclass(frozen=True, slots=True)
class ListItem:
    """One `List` entry; `key` is read by no target and `importance` decides which lines spill first."""

    key: str
    content: TextLike
    importance: Importance = Importance.NORMAL


@dataclass(frozen=True, slots=True)
class List(Renderable[RenderTarget]):
    """Bulleted or numbered lines, paginated under `key`.

    `page_size` pins every page to that many entries whether or not the budget is tight;
    `None` paginates only when the budget forces it.
    """

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
class Fields(Renderable[RenderTarget]):
    """Labelled values, never dropped whole.

    Embed fields where the target has them, continuing onto the next embed past the
    per-embed limit; elsewhere one line per field that condenses under pressure.
    """

    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class Column:
    """One `Table` column; `key` is read by no target."""

    key: str
    heading: TextLike


@dataclass(frozen=True, slots=True)
class Columns:
    """The column schema a `Table`'s rows are checked against."""

    columns: tuple[Column, ...]


@dataclass(frozen=True, slots=True)
class TableRow:
    """Cells in column order; `key` is read by no target."""

    key: str
    cells: tuple[TextLike, ...]


@dataclass(frozen=True, slots=True)
class Table(Renderable[RenderTarget]):
    """Rows drawn as `display` says (see `TableDisplay`); `key` carries the chosen strategy and page.

    Raises `ValueError` for no columns or a row whose cell count differs from the column count.
    """

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


@dataclass(frozen=True, slots=True)
class Note(Renderable[RenderTarget]):
    """Small print: an id, a timestamp, a caveat the reader may skip."""

    content: TextLike
    importance: Importance = Importance.LOW


@dataclass(frozen=True, slots=True)
class Quote(Renderable[RenderTarget]):
    """A `> ` block quote, `attribution` on an em-dash line below; never truncates."""

    content: TextLike
    attribution: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Code(Renderable[RenderTarget]):
    """A fenced code block that never truncates unless wrapped in `truncate`/`best_effort`."""

    content: str
    language: str = ""


@dataclass(frozen=True, slots=True)
class MediaItem:
    """One image; `description` is its alt text and `key` is read by no target.

    `spoiler` is honoured only on Components V2: a classic (embed) target raises
    `LayoutInvariantError` at plan time rather than show the image unblurred.
    """

    key: str
    url: str
    description: TextLike | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Figure(Renderable[RenderTarget]):
    """One image: an embed with `caption` as its footer on classic targets, a one-item gallery on Components V2."""

    media: MediaItem
    caption: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Media(Renderable[RenderTarget]):
    """Images drawn as `display` says (see `MediaDisplay`).

    A collection needs Components V2 galleries; a classic target raises `LayoutInvariantError`
    unless `display` is `FEATURED`.
    """

    items: tuple[MediaItem, ...]
    key: str
    display: MediaDisplay = MediaDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Summary:
    """The label of the button that opens and closes a `Details` region."""

    content: TextLike


@dataclass(frozen=True, slots=True)
class Details[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """A disclosure: one `summary` button, followed by `children` only while open."""

    key: str
    summary: Summary
    children: tuple[LayoutNode[RenderTargetT], ...]
    open: DisclosureOwnership = CLOSED


@dataclass(frozen=True, slots=True)
class ToggleEvent(ActionEvent):
    """The reader requested a new boolean value."""

    value: bool = False


@dataclass(frozen=True, slots=True)
class Toggle(Renderable[RenderTarget]):
    """One button reading `label: state`, where state is `on_label`/`off_label` or the chrome's on/off words."""

    key: str
    label: TextLike
    on: ToggleOwnership = OFF
    on_label: TextLike | None = None
    off_label: TextLike | None = None
    tone: Tone = Tone.NEUTRAL
    available: bool = True


@dataclass(frozen=True, slots=True)
class Download(Renderable[RenderTarget]):
    """A file offered where it is declared: a label line plus a file component on Components V2.

    A classic target uploads the asset as a plain attachment and reports the lost component
    as a degradation; `spoiler` there raises `LayoutInvariantError`. `label=None` uses
    `Chrome.download`.
    """

    key: str
    label: TextLike | None
    asset: Asset
    description: TextLike | None = None
    emphasis: Emphasis = Emphasis.NORMAL
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Status(Renderable[RenderTarget]):
    """One line prefixed with the emoji for `tone`; `emphasis` is not read."""

    content: TextLike
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL


@dataclass(frozen=True, slots=True)
class ProgressBar(Renderable[RenderTarget]):
    """A ten-cell text bar with a percentage; `value` is clamped to `0..maximum`."""

    value: float
    label: TextLike | None = None
    maximum: float = 1.0


@dataclass(frozen=True, slots=True)
class Roster(Renderable[RenderTarget]):
    """A slot-by-slot member list with one join button per slot.

    Each slot lowers to a heading with its `Chrome.slot_count`, its members as spillable
    lines, and a button: a mount-dispatched one when `on_join` is set, a routed one under
    `routes[slot.key]` otherwise, and none when neither is. `locked` disables every join
    button. Raises `ValueError` for an empty `key`, for both `on_join` and `routes`, or for
    `routes` whose keys are not exactly the slot keys.
    """

    key: str
    placement: RosterPlacement
    on_join: Callable[[SelectionEvent], Awaitable[None]] | None = None
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


@dataclass(frozen=True, slots=True)
class Grid(Renderable[RenderTarget]):
    """A grid of cells the reader picks one of; `on_pick` receives the cell key whatever the shape.

    One button per cell when `columns` fits a row and the buttons fit the message; otherwise
    a code-block matrix plus a coordinate select, paged once the available cells exceed one
    select. Raises `ValueError` for an empty `key` or a grid `validate_grid` rejects.
    """

    key: str
    cells: tuple[GridCell, ...]
    columns: int
    on_pick: Callable[[SelectionEvent], Awaitable[None]]
    flexibility: Flexibility = Flexibility.NORMAL

    def __post_init__(self) -> None:
        if not self.key:
            message = "Grid key must not be empty"
            raise ValueError(message)
        validate_grid(self.cells, self.columns)


@dataclass(frozen=True, slots=True)
class Metric(Renderable[RenderTarget]):
    """One `**label:** value unit` line; `value` is shown as given, not formatted."""

    value: int | float | str
    label: TextLike
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class Timestamp(Renderable[RenderTarget]):
    """An aware instant the target renders in the reader's timezone, as `style` says."""

    instant: datetime
    style: TimeStyle = TimeStyle.SHORT_DATETIME
    label: TextLike | None = None


@dataclass(frozen=True, slots=True)
class ZonedTimestamp(Renderable[RenderTarget]):
    """An instant shown in its own named timezone rather than the reader's."""

    value: ZonedDateTime
    label: TextLike | None = None


@dataclass(frozen=True, slots=True)
class FormTrigger(Renderable[RenderTarget]):
    """A button that opens `spec` as a modal.

    Lowers to a `FormButton` carrying the spec adapted to the target's capabilities and modal
    field limit; a target without modal forms raises `LayoutInvariantError` at plan time.
    """

    key: str
    label: TextLike
    spec: FormSpec
    on_submit: SubmitHandler
    mode: ActionMode = ActionMode.EXCLUSIVE
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    guard: Guard | None = None
    """Admission for the press that opens the form; the submission is not re-admitted."""
    record: History | None = None
    """History the successful submission enters under this trigger's label."""


@dataclass(frozen=True, slots=True)
class ActionControl:
    """A mount-dispatched press: a button, or one option of a select when its `ActionControls` groups."""

    key: str
    label: TextLike
    on_trigger: PressHandler
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True
    allow_grouping: bool | None = None
    """Whether the grouped strategy may fold this into a select; `None` allows it unless the
    action is `STRONG` or carries a `SUCCESS`/`WARNING`/`DANGER` tone."""
    mode: ActionMode = ActionMode.EXCLUSIVE
    guard: Guard | None = None
    """Whether this press may execute now. `available` is the render-time question."""
    busy: BusySpec | None = None
    """Busy indication for a handler slow enough that the reader needs to see it running."""
    record: History | None = None
    """History this press enters itself into, under `label`, before `on_trigger` runs."""


@dataclass(frozen=True, slots=True)
class Link:
    """A URL button; never grouped into a select, and `emphasis` is not read."""

    key: str
    label: TextLike
    url: str
    emphasis: Emphasis = Emphasis.NORMAL


@dataclass(frozen=True, slots=True)
class RoutedActionControl:
    """A control whose custom id is its state, dispatched by a router rather than a mount.

    For the buttons on mass-posted cards that must still work after a restart: no
    in-process handler, so a sessionless document may carry one. Build ``route_id`` with
    a `squid_ui.routing.Route`, which validates it against Discord's budget at
    authoring time rather than at send time.

    `ActionControl` remains the right node whenever a session is already in play; this one buys
    survival at the price of every guarantee the mount's funnel provides.
    """

    key: str
    label: TextLike
    route_id: str
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL
    available: bool = True


@dataclass(frozen=True, slots=True)
class ControlGroup:
    """Actions that share one select under the grouped strategy; `label` is its placeholder.

    Links and routed controls inside it stay individual buttons, since neither carries a
    binding a select could dispatch.
    """

    key: str
    controls: tuple[ActionControl | Link | RoutedActionControl, ...]
    label: TextLike | None = None


@dataclass(frozen=True, slots=True)
class ActionControls(Renderable[RenderTarget]):
    """The controls of one view, drawn as `display` says (see `ControlDisplay`).

    Actions are grouped by `ControlGroup`; a run of ungrouped actions shares a "default"
    group. Individual is one button each; grouped is one select per 25 eligible actions of a
    group, with ineligible ones (see `ActionControl.allow_grouping`) left as buttons. A group
    over 75 actions forces a paged select. `key` carries the chosen strategy and pages.
    """

    items: tuple[ActionControl | Link | RoutedActionControl | ControlGroup, ...]
    key: str
    display: ControlDisplay = ControlDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class Choice:
    """One `Choices` option; `key` is what a selection carries and an unavailable choice is not drawn."""

    key: str
    label: TextLike
    description: TextLike | None = None
    available: bool = True


@dataclass(frozen=True, slots=True)
class ChoiceEvent(ActionEvent):
    """A controlled `Choices` changed; `added`/`removed` are the difference from the value last rendered."""

    selected: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityEvent(ActionEvent):
    """A controlled `Entities` changed; `added`/`removed` are the difference from the value last rendered."""

    selected: tuple[EntityRef, ...] = ()
    added: tuple[EntityRef, ...] = ()
    removed: tuple[EntityRef, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityChoice:
    """One enumerated option for an `Entities` picker on a target without native entity selects."""

    ref: EntityRef
    label: TextLike
    description: TextLike | None = None
    available: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenEvent[ValueT](ActionEvent):
    """The reader asked to open something: one of N entries, or one disclosure."""

    opened: ValueT


@dataclass(frozen=True, slots=True)
class ScaleEvent(ActionEvent):
    """The reader picked one point on an ordinal scale."""

    value: int = 0


@dataclass(frozen=True, slots=True)
class Choices(Renderable[RenderTarget]):
    """A picker over `choices`: a button row for 2-5 available choices with `maximum=1`, else a select.

    A select shows 25 options per page and pages under `key`; a multi-select (`maximum > 1`)
    over 25 available choices raises `LayoutInvariantError` at plan time, because a
    selection cannot span pages.

    `minimum` defaults to 1, so the picker cannot be cleared to nothing without setting
    it to 0 explicitly; the button row always selects exactly one and ignores `minimum`.
    """

    key: str
    choices: tuple[Choice, ...]
    selection: ChoiceOwnership = UNSELECTED
    minimum: int = 1
    maximum: int = 1
    flexibility: Flexibility = Flexibility.NORMAL
    """Not read: `Choices` has no strategy axis for the cost model to price."""


@dataclass(frozen=True, slots=True)
class Entities(Renderable[RenderTarget]):
    """A picker the target resolves (user, role, channel) or, failing that, enumerates.

    A native entity select where the target has one; otherwise `choices` lower to a
    `Choices` over encoded refs, and an empty `choices` raises `LayoutInvariantError` at
    plan time. Raises `ValueError` for `conversation_types` on a non-conversation picker or
    a choice whose ref kind `entity_type` does not admit.
    """

    key: str
    entity_type: EntityType
    choices: tuple[EntityChoice, ...] = ()
    selection: EntityOwnership = NO_ENTITIES
    minimum: int = 1
    maximum: int = 1
    conversation_types: tuple[ConversationType, ...] = ()
    placeholder: TextLike | None = None
    flexibility: Flexibility = Flexibility.NORMAL
    """Not read: passed to the fallback `Choices`, which has no strategy axis either."""

    def __post_init__(self) -> None:
        if self.conversation_types and self.entity_type is not EntityType.CONVERSATION:
            message = "conversation_types is only valid for conversation entity pickers"
            raise ValueError(message)
        if any(not supports_entity(self.entity_type, choice.ref.kind) for choice in self.choices):
            message = f"fallback choice is incompatible with {self.entity_type.value} entity picker"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RoutedChoices(Renderable[RenderTarget]):
    """A stateless picker: always one `RoutedSelect` whose submission goes to `route_id`.

    No session, so no pagination: every available choice must fit one select, and none
    available raises `LayoutInvariantError` at plan time.
    """

    key: str
    choices: tuple[Choice, ...]
    route_id: str
    placeholder: TextLike | None = None
    minimum: int = 1
    maximum: int = 1
    available: bool = True


@dataclass(frozen=True, slots=True)
class ItemLabel:
    """An `Item`'s name: its overview line, its select option, and the heading of its opened view."""

    content: TextLike


@dataclass(frozen=True, slots=True)
class Item[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """One `Items` entry; `summary` follows the label on the overview line, `children` show only when opened."""

    key: str
    label: ItemLabel
    children: tuple[LayoutNode[RenderTargetT], ...]
    summary: TextLike | None = None


@dataclass(frozen=True, slots=True)
class Items[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Entries the reader opens one at a time.

    The overview is one line per item plus a select to open one, paged under `key` past
    25; the opened view is that item's heading, children, and a `Chrome.back` button.
    """

    key: str
    items: tuple[Item[RenderTargetT], ...]
    opened: ItemOwnership = UNOPENED
    display: ItemDisplay = ItemDisplay.AUTO
    flexibility: Flexibility = Flexibility.NORMAL


@dataclass(frozen=True, slots=True)
class NavOption:
    """One `Navigation` destination; an unavailable one is not drawn."""

    key: str
    label: TextLike
    available: bool = True


@dataclass(frozen=True, slots=True)
class NavigateEvent(ActionEvent):
    """A controlled `Navigation` was asked to move to `destination`."""

    destination: str = ""


@dataclass(frozen=True, slots=True)
class Navigation(Renderable[RenderTarget]):
    """Movement between the views of one message: a button row or a select, as `NavigationDisplay` says.

    The current destination is the primary-styled button or the select's default; a select
    pages under `key` past 25 destinations.
    """

    key: str
    options: tuple[NavOption, ...]
    current: NavOwnership = FIRST_OPTION
    """`None` means the first available destination."""
    display: NavigationDisplay = NavigationDisplay.AUTO
    flexibility: Flexibility = Flexibility.STABLE


@dataclass(frozen=True, slots=True)
class Truncated[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Text primitives under `node` may truncate; `keep` is `"head"` or `"tail"`."""

    node: LayoutNode[RenderTargetT]
    keep: str = "head"


@dataclass(frozen=True, slots=True)
class Spilled[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Line collections under `node` may drop their lowest-priority entries."""

    node: LayoutNode[RenderTargetT]


@dataclass(frozen=True, slots=True)
class OptionalContent[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """`node` may be dropped whole; `importance` orders it against the other droppable regions."""

    node: LayoutNode[RenderTargetT]
    importance: Importance = Importance.LOW


@dataclass(frozen=True, slots=True)
class FallbackContent[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Complete author-supplied representations of one region, best first.

    Only the chosen rung is lowered, so an unselected branch leaves no pagers, assets or
    session writes. Raises `ValueError` with no alternates.
    """

    primary: LayoutNode[RenderTargetT]
    alternates: tuple[LayoutNode[RenderTargetT], ...]

    def __post_init__(self) -> None:
        if not self.alternates:
            message = "FallbackContent needs at least one alternate"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class BestEffort[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """`node` may spill if it is a `List` or `Fields` and truncate otherwise.

    A `Budgeted` inside it is granted its `minimum` only after every hard floor, and is
    shortchanged rather than failing the plan.
    """

    node: LayoutNode[RenderTargetT]


@dataclass(frozen=True, slots=True)
class Budgeted[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Reserve and cap the character grant for one logical region.

    `minimum` is the floor the region is always granted, `preferred` its target, and
    `stretch` how far past `preferred` it may grow when characters are spare. Raises
    `ValueError` for a negative value or `minimum > preferred`.
    """

    node: LayoutNode[RenderTargetT]
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


@dataclass(frozen=True, slots=True)
class Unbreakable[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Keep every primitive produced by ``node`` together on a region page."""

    node: LayoutNode[RenderTargetT]


@dataclass(frozen=True, slots=True)
class KeepWithNext[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Forbid a region page break immediately after ``node``."""

    node: LayoutNode[RenderTargetT]


@dataclass(frozen=True, slots=True)
class Paged[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    """Paginate the direct children of a keyed heterogeneous region, `chars` per page.

    `min_fill` characters on a non-final page and `widows` children on the last are
    preferences the breaker violates as rarely as it can, not limits. `footer` receives the
    1-based page and page count in place of `Chrome.page_footer`. Raises `ValueError` for an
    empty `key`, `chars < 1`, a negative `min_fill`, or `widows < 1`.
    """

    node: LayoutNode[RenderTargetT]
    key: str
    chars: int
    min_fill: int = 0
    widows: int = 1
    initial: Literal["start", "end"] = "start"
    footer: Callable[[int, int], TextLike] | None = None

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


type SemanticNode[RenderTargetT = RenderTarget] = (
    Group[RenderTargetT]
    | Stack[RenderTargetT]
    | Cluster[RenderTargetT]
    | Themed[RenderTargetT]
    | Block[RenderTargetT]
    | Section[RenderTargetT]
    | Article[RenderTargetT]
    | Aside[RenderTargetT]
    | Details[RenderTargetT]
    | Items[RenderTargetT]
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
    | Toggle
    | Download
    | Status
    | ProgressBar
    | Roster
    | Grid
    | Metric
    | Timestamp
    | ZonedTimestamp
    | FormTrigger
    | ActionControls
    | Choices
    | Entities
    | RoutedChoices
    | Navigation
)
"""Everything the semantic vocabulary offers, in the dialects it can be drawn in.

Only the containers take the parameter. The rest are target-neutral by construction: a
`Table` or a `Roster` says what the information *means* and every dialect has some way to
draw it, which is the whole reason to author semantically. A leaf that grew a dialect
restriction would belong in `primitives`, not here.
"""

type Adaptation[RenderTargetT = RenderTarget] = (
    Truncated[RenderTargetT]
    | Spilled[RenderTargetT]
    | OptionalContent[RenderTargetT]
    | BestEffort[RenderTargetT]
    | Budgeted[RenderTargetT]
    | Unbreakable[RenderTargetT]
    | KeepWithNext[RenderTargetT]
    | Paged[RenderTargetT]
)
type AnyLayoutNode = LayoutNode[Any]
"""A node whose dialect is deliberately not tracked.

What the planner, the tree rewriters and the runtime walk. They rewrite whatever they are
handed and leave the dialect judgement to the target's dialect, so narrowing them to the
portable default would be a claim none of them makes.
"""

type PortableNode[RenderTargetT = RenderTarget] = (
    SemanticNode[RenderTargetT] | Adaptation[RenderTargetT] | FallbackContent[RenderTargetT]
)
"""The closed portable vocabulary: what every planner backend must answer for.

`LayoutNode` minus the open `Renderable` escape. A traversal that matches over this union
can be proven exhaustive, which `AnyLayoutNode` structurally cannot.
"""
type BuiltinLayoutNode = SemanticNode | Adaptation | FallbackContent | Node
"""Every layout node the framework itself ships, as classes an `isinstance` can test.

The closed spelling of `LayoutNode`, which is open at its `Renderable` arm. "Builtin" is
the operative word: a caller's own `Renderable` is a layout node and is deliberately not
one of these, which is what the Discord lowering needs to say when it meets one.
"""
type LayoutNode[RenderTargetT = RenderTarget] = Renderable[RenderTargetT]
"""Anything that may stand in the authored tree, in the dialects it can be drawn in.

Exactly `Renderable`, and named apart from it on purpose: `Renderable` is the capability a
class claims by inheriting it, while this is the stage a value is at. A field or parameter
wants to say "a node of the authored tree", not "something drawable", even though the two
are the same type.

Open at its only arm, so nothing may match over it. `PortableNode` is the closed union a
traversal proves itself exhaustive against, and `BuiltinLayoutNode` the one an `isinstance`
can test.
"""


def truncate[RenderTargetT = RenderTarget](
    node: LayoutNode[RenderTargetT], *, keep: str = "head"
) -> Truncated[RenderTargetT]:
    """Allow prose in ``node`` to truncate when no lossless plan fits."""
    return Truncated(node, keep)


def spill[RenderTargetT = RenderTarget](node: LayoutNode[RenderTargetT]) -> Spilled[RenderTargetT]:
    """Allow a static collection in ``node`` to omit its lowest-priority entries."""
    return Spilled(node)


def optional[RenderTargetT = RenderTarget](
    node: LayoutNode[RenderTargetT], *, importance: Importance = Importance.LOW
) -> OptionalContent[RenderTargetT]:
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
    steps down the ladder one rung at a time under component pressure. Raises `ValueError`
    with no alternates.
    """
    if not alternates:
        message = "sl.fallback() needs at least one alternate"
        raise ValueError(message)
    return FallbackContent(primary, alternates)


def best_effort[RenderTargetT = RenderTarget](node: LayoutNode[RenderTargetT]) -> BestEffort[RenderTargetT]:
    """Allow safe prose truncation and static collection spill, never consequential loss."""
    return BestEffort(node)


def budget[RenderTargetT = RenderTarget](
    node: LayoutNode[RenderTargetT], *, min: int, prefer: int, stretch: int = 0
) -> Budgeted[RenderTargetT]:
    """Give ``node`` a hard floor, preferred size, and lossless stretch band; see `Budgeted` for what it rejects."""
    return Budgeted(node, min, prefer, stretch)


def unbreakable[RenderTargetT = RenderTarget](node: LayoutNode[RenderTargetT]) -> Unbreakable[RenderTargetT]:
    """Keep ``node`` atomic when its containing region paginates."""
    return Unbreakable(node)


def keep_with_next[RenderTargetT = RenderTarget](node: LayoutNode[RenderTargetT]) -> KeepWithNext[RenderTargetT]:
    """Keep ``node`` off the bottom of a region page without its successor."""
    return KeepWithNext(node)


def paged[RenderTargetT = RenderTarget](
    node: LayoutNode[RenderTargetT],
    *,
    key: str,
    chars: int,
    min: int = 0,
    stretch: int = 0,
    min_fill: int = 0,
    widows: int = 1,
    initial: Literal["start", "end"] = "start",
    footer: Callable[[int, int], TextLike] | None = None,
) -> Budgeted[RenderTargetT]:
    """Page ``node`` at ``chars`` per page inside a `Budgeted` preferring ``chars``.

    Raises `ValueError` for what `Paged` and `Budgeted` reject.
    """
    region = Paged(node, key, chars, min_fill, widows, initial, footer)
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
