"""Variadic builders over the semantic IR — the recommended authoring surface.

The frozen dataclasses in :mod:`squid_ui.semantic` remain the IR and remain public;
these factories are sugar over them, normalizing what render code actually writes:
conditional children (``cond and node``), bare strings and t-strings.

Factories follow reading order: a semantic identity such as a heading, summary, item label,
table columns, or form label comes before the content it introduces. Runtime identity and
configuration remain keyword-only. ``key`` is required exactly where the runtime reads it
back — on nodes that own session state or custom ids. Records whose key no target reads today
(`Field`, `Column`, `TableRow`, `MediaItem`, `ListItem`) default it to ``""`` rather than
making authors invent identities that nothing consumes.

Collections are unpacked by the caller (``sl.section(*(sl.field(k, v) for k, v in rows))``).
Factories deliberately do not flatten a list argument: ``*`` already says it, says it at the
call site, and keeps a stray dict or generator from being silently absorbed.
"""

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from string.templatelib import Template
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAliasType, TypeIs, get_args, get_origin

from squid_ui.assets import Asset
from squid_ui.entity import ConversationType, EntityRef, EntityType
from squid_ui.forms import FormLike, SubmitHandler, bind_form
from squid_ui.grids import GridCell
from squid_ui.guards import Guard
from squid_ui.interactions import ActionMode, BusySpec, PressHandler, SelectionEvent
from squid_ui.palette import INHERIT, Accent, Palette
from squid_ui.rosters import RosterPlacement
from squid_ui.semantic import (
    CLOSED,
    FIRST_OPTION,
    NO_ENTITIES,
    OFF,
    UNOPENED,
    UNRATED,
    UNSELECTED,
    ActionControl,
    ActionControls,
    Article,
    Aside,
    Block,
    Choice,
    ChoiceEvent,
    ChoiceOwnership,
    Choices,
    Cluster,
    Code,
    Column,
    Columns,
    BuiltinLayoutNode,
    ControlDisplay,
    ControlGroup,
    Controlled,
    Details,
    DisclosureOwnership,
    Download,
    Emphasis,
    Entities,
    EntityChoice,
    EntityOwnership,
    Field,
    Fields,
    Figure,
    Flexibility,
    FormTrigger,
    Grid,
    Group,
    Heading,
    Importance,
    Item,
    ItemDisplay,
    ItemLabel,
    ItemOwnership,
    Items,
    LayoutNode,
    Link,
    List,
    ListItem,
    Media,
    MediaDisplay,
    MediaItem,
    Metric,
    Navigation,
    NavigationDisplay,
    NavOption,
    NavOwnership,
    Note,
    Paragraph,
    PortableNode,
    ProgressBar,
    Quote,
    Roster,
    RoutedActionControl,
    RoutedChoices,
    ScaleEvent,
    ScaleOwnership,
    Section,
    Stack,
    Status,
    Summary,
    Table,
    TableDisplay,
    TableRow,
    Themed,
    Timestamp,
    TimeStyle,
    Toggle,
    ToggleOwnership,
    Tone,
    Uncontrolled,
    ZonedTimestamp,
)
from squid_ui.tallies import TallyOption
from squid_ui.target_types import RenderTarget
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import Message, ResolvedText, TextLike, md

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History

type TextValue = TextLike | Template
"""Author text: trusted Markdown, already-resolved text, or a t-string to interpolate."""

type Conditional[ItemT] = ItemT | None | Literal[False]
"""One value, or an omitted one.

``None`` and ``False`` are skipped so ``cond and node`` composes directly. ``True`` is
deliberately *not* accepted: ``x and y`` evaluates to ``y`` or to the falsy ``x``, so a bare
``True`` can only come from a mistake. The same narrowness makes ``count and node`` — which
would render a literal ``0`` in looser designs — a type error rather than a surprise.
"""

type ChildLike[RenderTargetT = RenderTarget] = Conditional[LayoutNode[RenderTargetT] | TextLike | Template]
"""Anything acceptable in a container's child position; text is promoted to `Paragraph`.

`RenderTargetT` is what makes a nested dialect mistake a type error. A container factory solves it
from its children, so a `Panel` two levels down inside an otherwise portable document makes
the whole document `ComponentsV2Target`, and handing that to a classic target does not
type-check. See `docs/plans/squid-ui-redesign/spikes/73/` for the measurement that chose
plain inference over an overload ladder, and for the one case it does not catch.
"""


def _node_types(annotation: object) -> Iterator[type]:
    """Flatten a union of type aliases into the concrete classes it admits.

    The `get_origin` branch is not decoration: once the containers became generic in their
    dialect, `Stack[RenderTargetT]` stopped being a `type`, every container silently fell out of
    `_NODE_TYPES`, and `is_layout_node(sl.stack(...))` went quietly False -- which reads at
    runtime as "a stack is not content".
    """
    if isinstance(annotation, TypeAliasType):
        yield from _node_types(annotation.__value__)
    elif isinstance(annotation, UnionType):
        for member in get_args(annotation):
            yield from _node_types(member)
    elif isinstance(annotation, type):
        yield annotation
    elif isinstance(origin := get_origin(annotation), type):
        yield origin
    elif isinstance(origin, TypeAliasType):
        # A subscripted alias like `SemanticNode[RenderTargetT]`: its origin is the alias
        # itself, so recurse into it the same way an unsubscripted alias is flattened.
        yield from _node_types(origin)


# Derived from the union rather than hand-listed, so a new node type is accepted the moment
# it joins `BuiltinLayoutNode`.
_NODE_TYPES: tuple[type, ...] = tuple(_node_types(BuiltinLayoutNode))
_PORTABLE_TYPES: tuple[type, ...] = tuple(_node_types(PortableNode))


def is_layout_node(value: object) -> TypeIs[BuiltinLayoutNode]:
    """True when `value` is already a layout node, rather than text or a component.

    The public form of the derived `_NODE_TYPES` tuple: callers outside this module — the
    pattern content normalizers above all — need the membership test, never the tuple, and
    a predicate keeps the union's membership an implementation detail.
    """
    return isinstance(value, _NODE_TYPES)


def is_portable_node(value: object) -> TypeIs[PortableNode[Any]]:
    """True when `value` belongs to the closed portable vocabulary every planner answers for.

    Distinguishes the portable union from the `Renderable` escape hatch: a Discord primitive
    is a layout node but not a portable one.
    """
    return isinstance(value, _PORTABLE_TYPES)


def _text(value: TextValue) -> TextLike:
    return md(value) if isinstance(value, Template) else value


def _opt_text(value: TextValue | None) -> TextLike | None:
    return None if value is None else _text(value)


def _reject(value: object, origin: str, index: int) -> NoReturn:
    if value is True:
        detail = (
            "True is not content; `cond and node` evaluates to the node or to the condition, "
            "so only None and False can stand for an omitted child"
        )
    elif isinstance(value, str | ResolvedText | Template):
        # Only collection factories reject text; container children promote it to a Paragraph.
        detail = "text is not an entry here; build one with the matching factory"
    elif isinstance(value, Mapping):
        detail = f"a mapping is not content; unpack what you meant, e.g. {origin[:-1]}*mapping.values())"
    elif isinstance(value, Iterable):
        detail = f"a {type(value).__name__} is not content; unpack it, e.g. {origin[:-1]}*entries)"
    elif _is_component(value):
        detail = "components are placed with self.boundary(child, key=...)"
    else:
        detail = f"{type(value).__name__} is not content"
    message = f"{origin} argument {index}: {detail}"
    raise TypeError(message)


def _is_component(value: object) -> bool:
    # Imported on the error path only, so the sugar layer keeps depending on semantic + text.
    from squid_ui.runtime.component import Component

    return isinstance(value, Component)


def _children[RenderTargetT](
    values: tuple[ChildLike[RenderTargetT], ...], origin: str
) -> tuple[LayoutNode[RenderTargetT], ...]:
    collected: list[LayoutNode[RenderTargetT]] = []
    for index, value in enumerate(values):
        if value is None or value is False:
            continue
        if isinstance(value, str | ResolvedText | Template):
            collected.append(Paragraph(_text(value)))
        elif isinstance(value, _NODE_TYPES):
            collected.append(value)
        else:
            _reject(value, origin, index)
    return tuple(collected)


def _collect[ItemT](values: Iterable[Conditional[ItemT]], kinds: tuple[type, ...], origin: str) -> tuple[ItemT, ...]:
    """Drop omitted entries and reject anything that is not one of `kinds`.

    `kinds` is the erased runtime table, not `type[ItemT]`: a generic entry such as `Item`
    has no runtime class per render target, so tying the two would solve `ItemT` to the erased class
    and lose the caller's.
    """
    collected: list[ItemT] = []
    for index, value in enumerate(values):
        if value is None or value is False:
            continue
        if isinstance(value, kinds):
            collected.append(value)
        else:
            _reject(value, origin, index)
    return tuple(collected)


# --- containers ---------------------------------------------------------------------------


def group[RenderTargetT = RenderTarget](*children: ChildLike[RenderTargetT]) -> Group[RenderTargetT]:
    """Related content with no layout opinion; lowers to its children in place."""
    return Group(_children(children, "sl.group()"))


def stack[RenderTargetT = RenderTarget](*children: ChildLike[RenderTargetT]) -> Stack[RenderTargetT]:
    """Content read top to bottom."""
    return Stack(_children(children, "sl.stack()"))


def cluster[RenderTargetT = RenderTarget](*children: ChildLike[RenderTargetT]) -> Cluster[RenderTargetT]:
    """Content read as a set rather than a sequence."""
    return Cluster(_children(children, "sl.cluster()"))


def themed[RenderTargetT = RenderTarget](
    palette: Palette, *children: ChildLike[RenderTargetT]
) -> Themed[RenderTargetT]:
    """Apply a presentation palette to one semantic subtree."""
    return Themed(_children(children, "sl.themed()"), palette)


def block[RenderTargetT = RenderTarget](
    *children: ChildLike[RenderTargetT], accent: Accent = INHERIT
) -> Block[RenderTargetT]:
    """An untitled region; ``accent`` is a house-colour override."""
    return Block(_children(children, "sl.block()"), accent)


def section[RenderTargetT = RenderTarget](
    heading: Heading,
    *children: ChildLike[RenderTargetT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Section[RenderTargetT]:
    """A titled block of related content; ``accent`` is a house-colour override."""
    return Section(heading, _children(children, "sl.section()"), accent, thumbnail)


def article[RenderTargetT = RenderTarget](
    heading: Heading,
    *children: ChildLike[RenderTargetT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Article[RenderTargetT]:
    """A self-contained block that stands on its own."""
    return Article(heading, _children(children, "sl.article()"), accent, thumbnail)


def aside[RenderTargetT = RenderTarget](
    *children: ChildLike[RenderTargetT], tone: Tone = Tone.NEUTRAL
) -> Aside[RenderTargetT]:
    """Tangential or advisory content, coloured by tone."""
    return Aside(_children(children, "sl.aside()"), tone)


def controlled[ValueT, EventT](
    value: ValueT,
    on_change: Callable[[EventT], Awaitable[None]],
) -> Controlled[ValueT, EventT]:
    """Own this node's value yourself: it wins every render and ``on_change`` gets the writes."""
    return Controlled(value, on_change)


def uncontrolled[ValueT](initial: ValueT) -> Uncontrolled[ValueT]:
    """Let the engine own this node's value, seeded once with ``initial``."""
    return Uncontrolled(initial)


def details[RenderTargetT = RenderTarget](
    summary: Summary,
    *children: ChildLike[RenderTargetT],
    key: str,
    open: DisclosureOwnership = CLOSED,
) -> Details[RenderTargetT]:
    """Content the reader expands; ``key`` carries its disclosure state."""
    return Details(key, summary, _children(children, "sl.details()"), open)


def summary(content: TextValue) -> Summary:
    """The control text that identifies a `details` region."""
    return Summary(_text(content))


def form(
    label: TextValue,
    spec: FormLike,
    *,
    key: str,
    on_submit: SubmitHandler | None = None,
    mode: ActionMode | None = None,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    guard: Guard | None = None,
    record: History | None = None,
) -> FormTrigger:
    """A content control that presents a portable form.

    ``guard`` gates the press that opens the form; the submission that follows completes an
    already-admitted press and is not checked again.
    """
    resolved, handler, default_mode = bind_form(spec, on_submit)
    selected_mode = mode or default_mode
    if record is not None and selected_mode is ActionMode.PARALLEL_READ:
        message = "a parallel-read form submission changes nothing, so it has nothing to record"
        raise ValueError(message)
    return FormTrigger(key, _text(label), resolved, handler, selected_mode, tone, emphasis, guard, record)


def item[RenderTargetT = RenderTarget](
    label: ItemLabel, *children: ChildLike[RenderTargetT], key: str, summary: TextValue | None = None
) -> Item[RenderTargetT]:
    """One entry of an `items` collection."""
    return Item(key, label, _children(children, "sl.item()"), _opt_text(summary))


def item_label(content: TextValue) -> ItemLabel:
    """The identity shown for one `items` entry."""
    return ItemLabel(_text(content))


def items[RenderTargetT = RenderTarget](
    *entries: Conditional[Item[RenderTargetT]],
    key: str,
    opened: ItemOwnership = UNOPENED,
    display: ItemDisplay = ItemDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Items[RenderTargetT]:
    """A set of entries the reader browses; ``key`` carries the opened entry."""
    return Items(key, _collect(entries, (Item,), "sl.items()"), opened, display, flexibility)


# --- text and figures ---------------------------------------------------------------------


def heading(content: TextValue, *, level: int = 2, importance: Importance = Importance.HIGH) -> Heading:
    """A standalone heading."""
    return Heading(_text(content), level, importance)


def paragraph(content: TextValue, *, importance: Importance = Importance.NORMAL) -> Paragraph:
    """A block of prose."""
    return Paragraph(_text(content), importance)


def status(content: TextValue, *, tone: Tone = Tone.NEUTRAL, emphasis: Emphasis = Emphasis.NORMAL) -> Status:
    """A short outcome or state message."""
    return Status(_text(content), tone, emphasis)


def note(content: TextValue, *, importance: Importance = Importance.LOW) -> Note:
    """Small print: an id, a timestamp, a caveat the reader may skip."""
    return Note(_text(content), importance)


def code(content: str, *, language: str = "") -> Code:
    """Preformatted code or output."""
    return Code(content, language)


def quote(content: TextValue, *, attribution: TextValue | None = None) -> Quote:
    """Quoted text with optional attribution."""
    return Quote(_text(content), _opt_text(attribution))


def progress(value: float, *, label: TextValue | None = None, maximum: float = 1.0) -> ProgressBar:
    """Completion of a known-length task."""
    return ProgressBar(value, _opt_text(label), maximum)


def metric(value: int | float | str, label: TextValue, *, unit: str | None = None) -> Metric:
    """A single labelled quantity."""
    return Metric(value, _text(label), unit)


def timestamp(
    instant: datetime,
    *,
    style: TimeStyle = TimeStyle.SHORT_DATETIME,
    label: TextValue | None = None,
) -> Timestamp:
    """A typed instant; naive datetimes are rejected before rendering."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "sl.timestamp() requires an aware datetime"
        raise ValueError(message)
    return Timestamp(instant, style, _opt_text(label))


def zoned_timestamp(value: ZonedDateTime, *, label: TextValue | None = None) -> ZonedTimestamp:
    """An exact instant displayed with its IANA timezone identity."""
    return ZonedTimestamp(value, _opt_text(label))


def figure(media: MediaItem | str, *, caption: TextValue | None = None) -> Figure:
    """One image with an optional caption; a bare URL becomes the media item."""
    resolved = MediaItem("", media) if isinstance(media, str) else media
    return Figure(resolved, _opt_text(caption))


# --- records and their collections ---------------------------------------------------------


def field(
    label: TextValue,
    value: TextValue,
    *,
    key: str = "",
    importance: Importance = Importance.NORMAL,
    fallbacks: Iterable[TextValue] = (),
) -> Field:
    """One labelled value; ``fallbacks`` are shorter forms tried under budget pressure."""
    return Field(key, _text(label), _text(value), importance, tuple(_text(item) for item in fallbacks))


def fields(*entries: Conditional[Field]) -> Fields:
    """A block of labelled values that never loses a whole field."""
    return Fields(_collect(entries, (Field,), "sl.fields()"))


def bullet(content: TextValue, *, key: str = "", importance: Importance = Importance.NORMAL) -> ListItem:
    """One entry of a `bullets` list."""
    return ListItem(key, _text(content), importance)


def bullets(
    *entries: Conditional[ListItem | TextLike | Template],
    key: str,
    ordered: bool = False,
    page_size: int | None = None,
) -> List:
    """A bulleted or numbered list; bare text becomes an entry. ``key`` carries its page."""
    collected = _collect(entries, (ListItem, str, ResolvedText, Template), "sl.bullets()")
    return List(
        tuple(entry if isinstance(entry, ListItem) else bullet(entry) for entry in collected),
        key,
        ordered,
        page_size,
    )


def column(heading: TextValue, *, key: str = "") -> Column:
    """One column of a `table`."""
    return Column(key, _text(heading))


def columns(*entries: Conditional[Column]) -> Columns:
    """The ordered schema that precedes a `table`'s rows."""
    collected = _collect(entries, (Column,), "sl.columns()")
    if not collected:
        message = "sl.columns() needs at least one column"
        raise ValueError(message)
    return Columns(collected)


def table_row(*cells: TextValue, key: str = "") -> TableRow:
    """One row of a `table`; cells are positional, in column order."""
    return TableRow(key, tuple(_text(cell) for cell in cells))


def table(
    columns: Columns,
    *rows: Conditional[TableRow],
    key: str,
    display: TableDisplay = TableDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Table:
    """Tabular data; ``key`` carries the chosen representation and page."""
    return Table(
        columns,
        _collect(rows, (TableRow,), "sl.table()"),
        key,
        display,
        flexibility,
    )


def roster(
    placement: RosterPlacement,
    *,
    key: str,
    on_join: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    routes: Mapping[str, str] | None = None,
    locked: bool = False,
    show_waitlist: bool = True,
) -> Roster:
    """Render one host-owned roster allocation with active localized chrome."""
    return Roster(key, placement, on_join, routes, locked, show_waitlist)


def grid(
    *cells: Conditional[GridCell],
    key: str,
    columns: int,
    on_pick: Callable[[SelectionEvent], Awaitable[None]],
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Grid:
    """A selectable grid that adapts without changing its submitted cell keys."""
    return Grid(key, _collect(cells, (GridCell,), "sl.grid()"), columns, on_pick, flexibility)


def _tally_label(option: TallyOption) -> Message:
    prefix = "{emoji} " if option.emoji is not None else ""
    label = "**{label}**" if option.mine else "{label}"
    return Message(
        f"{prefix}{label} — {{count}}",
        {"emoji": option.emoji, "label": option.label, "count": option.count},
    )


def tally(
    options: Sequence[TallyOption],
    *,
    key: str,
    on_vote: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    route_id: str | None = None,
    total: int | None = None,
    show_bars: bool = True,
) -> Stack:
    """Render host-owned counts with optional mounted or routed selection."""
    if not options:
        message = "sl.tally() needs at least one option"
        raise ValueError(message)
    keys = {option.key for option in options}
    if len(keys) != len(options):
        message = "tally option keys must be unique"
        raise ValueError(message)
    if on_vote is not None and route_id is not None:
        message = "sl.tally() takes on_vote or route_id, not both"
        raise ValueError(message)
    resolved_total = sum(option.count for option in options) if total is None else total
    if resolved_total < 0 or any(option.count > resolved_total for option in options):
        message = "tally total must be non-negative and at least every option count"
        raise ValueError(message)

    bars: tuple[LayoutNode, ...] = (
        tuple(progress(option.count, label=option.label, maximum=max(1, resolved_total)) for option in options)
        if show_bars
        else ()
    )
    entries = tuple(choice(_tally_label(option), key=option.key) for option in options)
    if on_vote is not None:

        async def vote(event: ChoiceEvent) -> None:
            if event.selected:
                await on_vote(SelectionEvent(event.actor, event.responder, event.locale, event.context, event.selected))

        control: LayoutNode = choices(
            *entries,
            key=key,
            selection=controlled(tuple(option.key for option in options if option.mine), vote),
        )
    elif route_id is not None:
        control = routed_choices(*entries, route_id=route_id, key=key)
    else:
        control = bullets(
            *(bullet(_tally_label(option), key=option.key) for option in options),
            key=f"{key}.options",
        )
    return stack(*bars, control)


def media_item(url: str, *, key: str = "", description: TextValue | None = None) -> MediaItem:
    """One image of a `media` collection."""
    return MediaItem(key, url, _opt_text(description))


def media(
    *entries: Conditional[MediaItem | str],
    key: str,
    display: MediaDisplay = MediaDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Media:
    """A set of images; a bare URL becomes an item. ``key`` carries the chosen display."""
    collected = _collect(entries, (MediaItem, str), "sl.media()")
    return Media(
        tuple(media_item(entry) if isinstance(entry, str) else entry for entry in collected),
        key,
        display,
        flexibility,
    )


# --- controls -------------------------------------------------------------------------------


def action_control(
    label: TextValue,
    on_trigger: PressHandler,
    *,
    key: str,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
    allow_grouping: bool | None = None,
    mode: ActionMode = ActionMode.EXCLUSIVE,
    guard: Guard | None = None,
    busy: BusySpec | None = None,
    record: History | None = None,
) -> ActionControl:
    """A control that runs ``on_trigger``; ``key`` namespaces its custom id.

    ``guard`` decides whether a press may execute now; ``available`` decides whether the
    control is offered at all. A cooldown wants both.

    ``record`` opens the entry for this press, under ``label``, before the handler runs: an
    action whose whole commit is transactional state needs no `History.record` of its own.
    A world-changing action records one explicit `CompensationSpec`; doing so under
    ``record=`` raises `HistoryError` rather than making two entries.
    """
    if record is not None and mode is ActionMode.PARALLEL_READ:
        message = "a parallel-read action changes nothing, so it has nothing to record"
        raise ValueError(message)
    return ActionControl(
        key, _text(label), on_trigger, tone, emphasis, available, allow_grouping, mode, guard, busy, record
    )


def toggle(
    label: TextValue,
    *,
    key: str,
    on: ToggleOwnership = OFF,
    on_label: TextValue | None = None,
    off_label: TextValue | None = None,
    tone: Tone = Tone.NEUTRAL,
    available: bool = True,
) -> Toggle:
    """A boolean control; ``on`` declares whether the author or session owns its state."""
    return Toggle(key, _text(label), on, _opt_text(on_label), _opt_text(off_label), tone, available)


def download(
    label: TextValue | None,
    asset: Asset,
    *,
    key: str,
    description: TextValue | None = None,
    emphasis: Emphasis = Emphasis.NORMAL,
) -> Download:
    """Offer an inline-declared asset through a visible download control."""
    return Download(key, _opt_text(label), asset, _opt_text(description), emphasis)


def link(label: TextValue, url: str, *, key: str, emphasis: Emphasis = Emphasis.NORMAL) -> Link:
    """A control that opens ``url``."""
    return Link(key, _text(label), url, emphasis)


def routed_action_control(
    label: TextValue,
    route_id: str,
    *,
    key: str,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
) -> RoutedActionControl:
    """A control the router dispatches, surviving the process that drew it.

    Build ``route_id`` with a `squid_ui.routing.Route` rather than by hand, so an id
    over Discord's budget fails here and not at send time.
    """
    return RoutedActionControl(key, _text(label), route_id, tone, emphasis, available)


def control_group(
    *entries: Conditional[ActionControl | Link | RoutedActionControl], key: str, label: TextValue | None = None
) -> ControlGroup:
    """Controls that belong together and degrade together."""
    return ControlGroup(
        key, _collect(entries, (ActionControl, Link, RoutedActionControl), "sl.control_group()"), _opt_text(label)
    )


def action_controls(
    *entries: Conditional[ActionControl | Link | RoutedActionControl | ControlGroup],
    key: str,
    display: ControlDisplay = ControlDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> ActionControls:
    """The controls offered by a view; ``key`` carries the chosen presentation."""
    return ActionControls(
        _collect(entries, (ActionControl, Link, RoutedActionControl, ControlGroup), "sl.action_controls()"),
        key,
        display,
        flexibility,
    )


def choice(label: TextValue, *, key: str, description: TextValue | None = None, available: bool = True) -> Choice:
    """One option of a `choices` control; ``key`` is the value it submits."""
    return Choice(key, _text(label), _opt_text(description), available)


def choices(
    *entries: Conditional[Choice],
    key: str,
    selection: ChoiceOwnership = UNSELECTED,
    minimum: int = 1,
    maximum: int = 1,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Choices:
    """A control that selects among options; ``key`` namespaces its custom id."""
    return Choices(
        key,
        _collect(entries, (Choice,), "sl.choices()"),
        selection,
        minimum,
        maximum,
        flexibility,
    )


def entity_choice(
    ref: EntityRef,
    label: TextValue,
    *,
    description: TextValue | None = None,
    available: bool = True,
) -> EntityChoice:
    """One enumerated fallback for an entity picker."""
    return EntityChoice(ref, _text(label), _opt_text(description), available)


def entities(
    *entries: Conditional[EntityChoice],
    key: str,
    entity_type: EntityType,
    selection: EntityOwnership = NO_ENTITIES,
    minimum: int = 1,
    maximum: int = 1,
    conversation_types: tuple[ConversationType, ...] = (),
    placeholder: TextValue | None = None,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Entities:
    """Select frontend-resolved entities, optionally with an enumerated fallback."""
    return Entities(
        key,
        entity_type,
        _collect(entries, (EntityChoice,), "sl.entities()"),
        selection,
        minimum,
        maximum,
        conversation_types,
        _opt_text(placeholder),
        flexibility,
    )


def rating(
    *,
    key: str,
    maximum: int = 5,
    value: ScaleOwnership = UNRATED,
    labels: Mapping[int, TextValue] | None = None,
) -> Choices:
    """An ordinal 1-to-``maximum`` picker; five or fewer points render as a star row.

    No new semantic node: `choices` with ``maximum=1`` already lowers to the row a rating
    wants. This owns the two things that would otherwise be hand-rolled every time — the
    star labels and the string-to-`int` round trip — and hands the handler a typed
    `ScaleEvent` rather than the option key it was submitted as.
    """
    if maximum < 2:
        message = "sl.rating() needs a maximum of at least 2"
        raise ValueError(message)
    points = range(1, maximum + 1)
    named = {} if labels is None else labels
    entries = tuple(
        Choice(str(point), _text(named[point]) if point in named else _text(_stars(point, maximum))) for point in points
    )
    if isinstance(value, Uncontrolled):
        selection: ChoiceOwnership = Uncontrolled(() if value.initial is None else (str(value.initial),))
    else:
        chosen = value.value
        on_change = value.on_change

        async def rate(event: ChoiceEvent) -> None:
            if not event.selected:
                return
            await on_change(
                ScaleEvent(
                    event.actor,
                    event.responder,
                    event.locale,
                    event.context,
                    int(event.selected[0]),
                )
            )

        selection = Controlled((), rate) if chosen is None else Controlled((str(chosen),), rate)
    return Choices(key, entries, selection, minimum=1, maximum=1)


def _stars(point: int, maximum: int) -> str:
    """The default label for one scale point.

    Cumulative stars while the control is still a button row; above that a select of ten
    star strings is unreadable, so the number speaks for itself.
    """
    return "\N{BLACK STAR}" * point if maximum <= 5 else str(point)


def routed_choices(
    *entries: Conditional[Choice],
    route_id: str,
    key: str,
    placeholder: TextValue | None = None,
    minimum: int = 1,
    maximum: int = 1,
    available: bool = True,
) -> RoutedChoices:
    """A stateless string picker dispatched by ``route_id`` with selected choice keys."""
    return RoutedChoices(
        key,
        _collect(entries, (Choice,), "sl.routed_choices()"),
        route_id,
        _opt_text(placeholder),
        minimum,
        maximum,
        available,
    )


def nav_option(label: TextValue, *, key: str, available: bool = True) -> NavOption:
    """One place `navigation` can go."""
    return NavOption(key, _text(label), available)


def navigation(
    *entries: Conditional[NavOption],
    key: str,
    current: NavOwnership = FIRST_OPTION,
    display: NavigationDisplay = NavigationDisplay.AUTO,
    flexibility: Flexibility = Flexibility.STABLE,
) -> Navigation:
    """Movement between the views of one message."""
    return Navigation(
        key,
        _collect(entries, (NavOption,), "sl.navigation()"),
        current,
        display,
        flexibility,
    )


__all__ = [
    "ChildLike",
    "Conditional",
    "TextValue",
    "action_control",
    "action_controls",
    "article",
    "aside",
    "block",
    "bullet",
    "bullets",
    "choice",
    "choices",
    "cluster",
    "code",
    "column",
    "columns",
    "control_group",
    "controlled",
    "details",
    "download",
    "entities",
    "entity_choice",
    "field",
    "fields",
    "figure",
    "form",
    "grid",
    "group",
    "heading",
    "item",
    "item_label",
    "items",
    "link",
    "media",
    "media_item",
    "metric",
    "nav_option",
    "navigation",
    "note",
    "paragraph",
    "progress",
    "quote",
    "rating",
    "roster",
    "routed_action_control",
    "routed_choices",
    "section",
    "stack",
    "status",
    "summary",
    "table",
    "table_row",
    "tally",
    "themed",
    "timestamp",
    "toggle",
    "uncontrolled",
    "zoned_timestamp",
]
