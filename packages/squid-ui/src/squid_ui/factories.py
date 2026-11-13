"""Build semantic layout nodes using shorthand suited to Component.render methods.

These factories construct the public records in squid_ui.semantic. Containers accept
layout nodes, turn bare strings and t-strings into paragraphs, and omit None or False
children. For example, ``sl.stack("Details", show_note and sl.note("Draft"))`` includes
the note only when show_note is true.

Factories follow reading order: a semantic identity such as a heading, summary, item label,
table columns, or form label comes before the content it introduces. Runtime identity and
configuration remain keyword-only. ``key`` is required exactly where the runtime reads it
back — on nodes that own session state or custom ids. Records whose key no target reads today
(`Field`, `Column`, `TableRow`, `MediaItem`, `ListItem`) default it to ``""`` rather than
making authors invent identities that nothing consumes.

Collections are unpacked by the caller (``sl.fields(*(sl.field(k, v) for k, v in rows))``).
Factories do not flatten a list argument: explicit unpacking keeps a stray dictionary
or generator from being silently treated as content.
"""

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from string.templatelib import Template
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAliasType, TypeIs, cast, get_args, get_origin

from squid_i18n import NUMBER, plain, tr
from squid_ui.assets import Asset
from squid_ui.entity import ConversationType, EntityRef, EntityType
from squid_ui.forms import FormLike, SubmitHandler, bind_form
from squid_ui.grids import GridCell
from squid_ui.guards import Guard
from squid_ui.interactions import ActionMode, BusySpec, PressHandler, SelectionEvent
from squid_ui.palette import INHERIT, Accent, Palette
from squid_ui.primitives.constraints import FIRST_PAGE, PageOwnership
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
    BuiltinLayoutNode,
    Choice,
    ChoiceEvent,
    ChoiceOwnership,
    Choices,
    Cluster,
    Code,
    Column,
    Columns,
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
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import Message, ResolvedText, TextLike, md

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History

type TextValue = TextLike | Template
"""Author text: trusted Markdown, already-resolved text, or a t-string to interpolate."""

type Conditional[ItemT] = ItemT | Literal[False] | None
"""One value, or an omitted one.

``None`` and ``False`` are skipped so ``cond and node`` composes directly. In a container's
child position an empty string is skipped too, because ``text and sl.paragraph(text)``
evaluates to ``""`` when the text is empty. ``True`` is deliberately *not* accepted:
``x and y`` evaluates to ``y`` or to the falsy ``x``, so a bare ``True`` can only come from a
mistake. The same narrowness makes ``count and node`` — which would render a literal ``0`` in
looser designs — a type error rather than a surprise.
"""

type ChildLike[RenderTargetT = RenderTarget, ContextT = object] = Conditional[
    LayoutNode[RenderTargetT, ContextT] | TextLike | Template
]
"""Anything acceptable in a container's child position; text is promoted to `Paragraph`.

`RenderTargetT` is what makes a nested dialect mistake a type error. A container factory solves it
from its children, so a `Panel` two levels down inside an otherwise portable document makes
the whole document `ComponentsV2Target`, and handing that to a classic target does not
type-check. See `docs/plans/squid-ui-redesign/spikes/73/` for the measurement that chose
plain inference over an overload ladder, and for the one case it does not catch.
"""


def _node_types(annotation: object) -> Iterator[type]:
    """Flatten aliases and unions into concrete classes for runtime membership tests.

    Parameterized containers have a runtime origin rather than being classes themselves;
    follow that origin so generic layout nodes remain in the accepted vocabulary.
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
_BUILTIN_TYPES: tuple[type, ...] = tuple(_node_types(BuiltinLayoutNode))
_PORTABLE_TYPES: tuple[type, ...] = tuple(_node_types(PortableNode))


def is_layout_node(value: object) -> TypeIs[Renderable[Any, Any]]:
    """Whether value inherits Renderable, including application-defined layout nodes.

    Use is_builtin_layout_node when a traversal requires the framework's closed union.
    """
    return isinstance(value, Renderable)


def is_builtin_layout_node(value: object) -> TypeIs[BuiltinLayoutNode]:
    """Whether value belongs to the framework-defined layout vocabulary."""
    return isinstance(value, _BUILTIN_TYPES)


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
    elif isinstance(value, str | ResolvedText | Message | Template):
        # Only collection factories reject text; container children promote it to a Paragraph.
        detail = "text is not an entry here; build one with the matching factory"
    elif isinstance(value, Mapping):
        detail = f"a mapping is not content; unpack what you meant, e.g. {origin[:-1]}*mapping.values())"
    elif isinstance(value, Iterable):
        detail = f"a {type(value).__name__} is not content; unpack it, e.g. {origin[:-1]}*entries)"
    else:
        detail = f"{type(value).__name__} is not content"
    message = f"{origin} argument {index}: {detail}"
    raise TypeError(message)


def _children[RenderTargetT = RenderTarget, ContextT = object](
    values: tuple[ChildLike[RenderTargetT, ContextT], ...], origin: str
) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
    collected: list[LayoutNode[RenderTargetT, ContextT]] = []
    for index, value in enumerate(values):
        if value is None or value is False or value == "":
            continue
        if isinstance(value, str | ResolvedText | Message | Template):
            # A paragraph renders on every target, but its own annotation names the base
            # `RenderTarget`, so it cannot be spelled as satisfying an arbitrary one.
            collected.append(cast(LayoutNode[RenderTargetT, ContextT], Paragraph(_text(value))))
        elif is_layout_node(value):
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


def group[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
) -> Group[RenderTargetT, ContextT]:
    """Related content with no layout opinion; lowers to its children in place."""
    return Group(_children(children, "sl.group()"))


def stack[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
) -> Stack[RenderTargetT, ContextT]:
    """Content read top to bottom."""
    return Stack(_children(children, "sl.stack()"))


def cluster[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
) -> Cluster[RenderTargetT, ContextT]:
    """Content read as a set rather than a sequence."""
    return Cluster(_children(children, "sl.cluster()"))


def themed[RenderTargetT = RenderTarget, ContextT = object](
    palette: Palette, *children: ChildLike[RenderTargetT, ContextT]
) -> Themed[RenderTargetT, ContextT]:
    """Apply a presentation palette to one semantic subtree."""
    return Themed(_children(children, "sl.themed()"), palette)


def block[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT], accent: Accent = INHERIT
) -> Block[RenderTargetT, ContextT]:
    """An untitled region; ``accent`` is a house-colour override."""
    return Block(_children(children, "sl.block()"), accent)


def section[RenderTargetT = RenderTarget, ContextT = object](
    heading: Heading,
    *children: ChildLike[RenderTargetT, ContextT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Section[RenderTargetT, ContextT]:
    """A titled block of related content; ``accent`` is a house-colour override."""
    return Section(heading, _children(children, "sl.section()"), accent, thumbnail)


def article[RenderTargetT = RenderTarget, ContextT = object](
    heading: Heading,
    *children: ChildLike[RenderTargetT, ContextT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Article[RenderTargetT, ContextT]:
    """A self-contained block that stands on its own."""
    return Article(heading, _children(children, "sl.article()"), accent, thumbnail)


def aside[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT], tone: Tone = Tone.NEUTRAL
) -> Aside[RenderTargetT, ContextT]:
    """Tangential or advisory content, coloured by tone."""
    return Aside(_children(children, "sl.aside()"), tone)


def controlled[ValueT, EventT](
    value: ValueT,
    on_change: Callable[[EventT], Awaitable[None]],
) -> Controlled[ValueT, EventT]:
    """Own this node's value yourself: it wins every render and ``on_change`` gets the writes."""
    return Controlled(value, on_change)


def uncontrolled[ValueT](initial: ValueT) -> Uncontrolled[ValueT]:
    """Store this node's value in PresentationState, using initial when its key is absent."""
    return Uncontrolled(initial)


def details[RenderTargetT = RenderTarget, ContextT = object](
    summary: Summary,
    *children: ChildLike[RenderTargetT, ContextT],
    key: str,
    open: DisclosureOwnership[ContextT] = CLOSED,
) -> Details[RenderTargetT, ContextT]:
    """Content the reader expands; ``key`` carries its disclosure state."""
    return Details(key, summary, _children(children, "sl.details()"), open)


def summary(content: TextValue) -> Summary:
    """The control text that identifies a `details` region."""
    return Summary(_text(content))


def form[ContextT = object](
    label: TextValue,
    spec: FormLike[ContextT],
    *,
    key: str,
    on_submit: SubmitHandler[ContextT] | None = None,
    mode: ActionMode | None = None,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    guard: Guard | None = None,
    record: History | None = None,
) -> FormTrigger[ContextT]:
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


def item[RenderTargetT = RenderTarget, ContextT = object](
    label: ItemLabel, *children: ChildLike[RenderTargetT, ContextT], key: str, summary: TextValue | None = None
) -> Item[RenderTargetT, ContextT]:
    """One entry of an `items` collection."""
    return Item(key, label, _children(children, "sl.item()"), _opt_text(summary))


def item_label(content: TextValue) -> ItemLabel:
    """The identity shown for one `items` entry."""
    return ItemLabel(_text(content))


def items[RenderTargetT = RenderTarget, ContextT = object](
    *entries: Conditional[Item[RenderTargetT, ContextT]],
    key: str,
    opened: ItemOwnership[ContextT] = UNOPENED,
    display: ItemDisplay = ItemDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Items[RenderTargetT, ContextT]:
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


def figure(media: MediaItem | str | Asset, *, caption: TextValue | None = None) -> Figure:
    """One image with an optional caption, from a URL or asset."""
    resolved = media_item(media) if isinstance(media, str | Asset) else media
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
    page: PageOwnership = FIRST_PAGE,
) -> List:
    """A bulleted or numbered list; bare text becomes an entry. ``key`` carries its page."""
    collected = _collect(entries, (ListItem, str, ResolvedText, Template), "sl.bullets()")
    return List(
        tuple(entry if isinstance(entry, ListItem) else bullet(entry) for entry in collected),
        key,
        ordered,
        page_size,
        page,
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


def roster[ContextT = object](
    placement: RosterPlacement,
    *,
    key: str,
    on_join: Callable[[SelectionEvent[ContextT]], Awaitable[None]] | None = None,
    routes: Mapping[str, str] | None = None,
    locked: bool = False,
    show_waitlist: bool = True,
) -> Roster[ContextT]:
    """Render one host-owned roster allocation with active localized chrome."""
    return Roster(key, placement, on_join, routes, locked, show_waitlist)


def grid[ContextT = object](
    *cells: Conditional[GridCell],
    key: str,
    columns: int,
    on_pick: Callable[[SelectionEvent[ContextT]], Awaitable[None]],
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Grid[ContextT]:
    """A selectable grid that adapts without changing its submitted cell keys."""
    return Grid(key, _collect(cells, (GridCell,), "sl.grid()"), columns, on_pick, flexibility)


def _tally_label(option: TallyOption) -> Message:
    label = Message.join("", ("**", option.label, "**")) if option.mine else option.label
    count = option.count
    result = tr(t"ui-tally-option = {label} — {NUMBER(count)}")
    return Message.join(" ", (plain(option.emoji), result)) if option.emoji is not None else result


def tally[ContextT = object](
    options: Sequence[TallyOption],
    *,
    key: str,
    on_vote: Callable[[SelectionEvent[ContextT]], Awaitable[None]] | None = None,
    route_id: str | None = None,
    total: int | None = None,
    show_bars: bool = True,
) -> Stack[RenderTarget, ContextT]:
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
        vote = _TallySelection(on_vote)

        control: LayoutNode[RenderTarget, ContextT] = choices(
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


def media_item(source: str | Asset, *, key: str = "", description: TextValue | None = None) -> MediaItem:
    """One media preview, from a URL or an asset declared at its point of use."""
    return MediaItem(key, source, _opt_text(description))


def media(
    *entries: Conditional[MediaItem | str | Asset],
    key: str,
    display: MediaDisplay = MediaDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Media:
    """A set of media previews; a bare URL or asset becomes an item. ``key`` carries the chosen display."""
    collected = _collect(entries, (MediaItem, str, Asset), "sl.media()")
    return Media(
        tuple(media_item(entry) if isinstance(entry, str | Asset) else entry for entry in collected),
        key,
        display,
        flexibility,
    )


# --- controls -------------------------------------------------------------------------------


def action_control[ContextT = object](
    label: TextValue,
    on_trigger: PressHandler[ContextT],
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
) -> ActionControl[ContextT]:
    """A control that runs ``on_trigger``; ``key`` namespaces its custom id.

    ``available`` controls the rendered enabled state; ``guard`` checks admission when
    a press arrives. A disabled appearance does not replace an authorization check.

    ``record`` opens the entry for this press, under ``label``, before the handler runs: an
    action whose whole commit is transactional state needs no `History.record` of its own.
    To undo external effects as well, call History.record with a CompensationSpec
    inside the handler instead. Combining that call with ``record=`` raises HistoryError
    because both would reserve the same history entry.
    """
    if record is not None and mode is ActionMode.PARALLEL_READ:
        message = "a parallel-read action changes nothing, so it has nothing to record"
        raise ValueError(message)
    return ActionControl(
        key, _text(label), on_trigger, tone, emphasis, available, allow_grouping, mode, guard, busy, record
    )


def toggle[ContextT = object](
    label: TextValue,
    *,
    key: str,
    on: ToggleOwnership[ContextT] = OFF,
    on_label: TextValue | None = None,
    off_label: TextValue | None = None,
    tone: Tone = Tone.NEUTRAL,
    available: bool = True,
) -> Toggle[ContextT]:
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


def control_group[ContextT = object](
    *entries: Conditional[ActionControl[ContextT] | Link | RoutedActionControl],
    key: str,
    label: TextValue | None = None,
) -> ControlGroup[ContextT]:
    """Controls that belong together and degrade together."""
    return ControlGroup(
        key, _collect(entries, (ActionControl, Link, RoutedActionControl), "sl.control_group()"), _opt_text(label)
    )


def action_controls[ContextT = object](
    *entries: Conditional[
        ActionControl[ContextT]
        | Link
        | RoutedActionControl
        | Toggle[ContextT]
        | FormTrigger[ContextT]
        | ControlGroup[ContextT]
    ],
    key: str,
    display: ControlDisplay = ControlDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> ActionControls[ContextT]:
    """The controls offered by a view, laid out in order; ``key`` carries the chosen presentation."""
    return ActionControls(
        _collect(
            entries,
            (ActionControl, Link, RoutedActionControl, Toggle, FormTrigger, ControlGroup),
            "sl.action_controls()",
        ),
        key,
        display,
        flexibility,
    )


def choice(label: TextValue, *, key: str, description: TextValue | None = None, available: bool = True) -> Choice:
    """One option of a `choices` control; ``key`` is the value it submits."""
    return Choice(key, _text(label), _opt_text(description), available)


def choices[ContextT = object](
    *entries: Conditional[Choice],
    key: str,
    selection: ChoiceOwnership[ContextT] = UNSELECTED,
    minimum: int = 1,
    maximum: int = 1,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Choices[ContextT]:
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


def entities[ContextT = object](
    *entries: Conditional[EntityChoice],
    key: str,
    entity_type: EntityType,
    selection: EntityOwnership[ContextT] = NO_ENTITIES,
    minimum: int = 1,
    maximum: int = 1,
    conversation_types: tuple[ConversationType, ...] = (),
    placeholder: TextValue | None = None,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Entities[ContextT]:
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


def rating[ContextT = object](
    *,
    key: str,
    maximum: int = 5,
    value: ScaleOwnership[ContextT] = UNRATED,
    labels: Mapping[int, TextValue] | None = None,
) -> Choices[ContextT]:
    """Build a single-select ordinal scale from 1 through maximum.

    Default labels use cumulative stars through five points and numbers above five.
    Controlled changes are converted from choice keys into integer ScaleEvent values;
    the target planner chooses the actual control representation.
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
        selection: ChoiceOwnership[ContextT] = Uncontrolled(() if value.initial is None else (str(value.initial),))
    else:
        chosen = value.value
        on_change = value.on_change

        rate = _RatingSelection(on_change)

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
    selected: tuple[str, ...] = (),
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
        selected,
    )


def nav_option(label: TextValue, *, key: str, available: bool = True) -> NavOption:
    """One place `navigation` can go."""
    return NavOption(key, _text(label), available)


def navigation[ContextT = object](
    *entries: Conditional[NavOption],
    key: str,
    current: NavOwnership[ContextT] = FIRST_OPTION,
    display: NavigationDisplay = NavigationDisplay.AUTO,
    flexibility: Flexibility = Flexibility.STABLE,
) -> Navigation[ContextT]:
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


@dataclass(frozen=True, slots=True)
class _TallySelection[ContextT]:
    callback: Callable[[SelectionEvent[ContextT]], Awaitable[None]]

    async def __call__(self, event: ChoiceEvent[ContextT]) -> None:
        if event.selected:
            await self.callback(
                SelectionEvent(
                    event.actor, event.responder, event.locale, event.metadata, event.selected, context=event.context
                )
            )


@dataclass(frozen=True, slots=True)
class _RatingSelection[ContextT]:
    callback: Callable[[ScaleEvent[ContextT]], Awaitable[None]]

    async def __call__(self, event: ChoiceEvent[ContextT]) -> None:
        if event.selected:
            await self.callback(
                ScaleEvent(
                    event.actor,
                    event.responder,
                    event.locale,
                    event.metadata,
                    int(event.selected[0]),
                    context=event.context,
                )
            )
