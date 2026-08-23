"""Variadic builders over the semantic IR — the recommended authoring surface.

The frozen dataclasses in :mod:`squid_layouts.semantic` remain the IR and remain public;
these factories are sugar over them, normalizing what render code actually writes:
conditional children (``cond and node``), bare strings and t-strings.

Every factory has one shape: **content is positional, identity and configuration are
keyword-only**. ``key`` is required exactly where the runtime reads it back — on nodes that
own session state or custom ids. Records whose key no target reads today (`Field`, `Column`,
`TableRow`, `MediaItem`, `ListItem`) default it to ``""`` rather than making authors invent
identities that nothing consumes.

Collections are unpacked by the caller (``sl.section(*(sl.field(k, v) for k, v in rows))``).
Factories deliberately do not flatten a list argument: ``*`` already says it, says it at the
call site, and keeps a stray dict or generator from being silently absorbed.
"""

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from datetime import datetime
from string.templatelib import Template
from types import UnionType
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAliasType, get_args

from squid_layouts.actions import ActionEvent, ActionPolicy, Feedback
from squid_layouts.assets import Asset
from squid_layouts.entities import ChannelType, EntityRef, EntityType
from squid_layouts.forms import FormLike, SubmitHandler, bind_form
from squid_layouts.guards import Guard
from squid_layouts.palette import INHERIT, Accent, Palette
from squid_layouts.semantic import (
    CLOSED,
    FIRST_DESTINATION,
    NO_ENTITIES,
    OFF,
    UNOPENED,
    UNRATED,
    UNSELECTED,
    Action,
    ActionDisplay,
    ActionGroup,
    Actions,
    Article,
    Aside,
    Choice,
    ChoiceEvent,
    ChoiceOwnership,
    Choices,
    Cluster,
    Code,
    Column,
    Controlled,
    Destination,
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
    Group,
    Heading,
    Importance,
    Item,
    ItemDisplay,
    ItemOwnership,
    Items,
    LayoutNode,
    Link,
    List,
    ListItem,
    Managed,
    Measure,
    Media,
    MediaDisplay,
    MediaItem,
    Navigation,
    NavigationDisplay,
    NavOwnership,
    Note,
    Paragraph,
    Progress,
    Quote,
    RoutedAction,
    RoutedChoices,
    ScaleEvent,
    ScaleOwnership,
    Section,
    Stack,
    Status,
    Table,
    TableDisplay,
    TableRow,
    Themed,
    Timestamp,
    TimeStyle,
    Toggle,
    ToggleOwnership,
    Tone,
    ZonedTimestamp,
)
from squid_layouts.temporal import ZonedDateTime
from squid_layouts.text import ResolvedText, TextLike, md

if TYPE_CHECKING:
    from squid_layouts.runtime.history import History

type TextValue = TextLike | Template
"""Author text: trusted Markdown, already-resolved text, or a t-string to interpolate."""

type Conditional[ItemT] = ItemT | None | Literal[False]
"""One value, or an omitted one.

``None`` and ``False`` are skipped so ``cond and node`` composes directly. ``True`` is
deliberately *not* accepted: ``x and y`` evaluates to ``y`` or to the falsy ``x``, so a bare
``True`` can only come from a mistake. The same narrowness makes ``count and node`` — which
would render a literal ``0`` in looser designs — a type error rather than a surprise.
"""

type ChildLike = Conditional[LayoutNode | TextLike | Template]
"""Anything acceptable in a container's child position; text is promoted to `Paragraph`."""


def _node_types(annotation: object) -> Iterator[type]:
    """Flatten a union of type aliases into the concrete classes it admits."""
    if isinstance(annotation, TypeAliasType):
        yield from _node_types(annotation.__value__)
    elif isinstance(annotation, UnionType):
        for member in get_args(annotation):
            yield from _node_types(member)
    elif isinstance(annotation, type):
        yield annotation


# Derived from the union rather than hand-listed, so a new node type is accepted the moment
# it joins `LayoutNode`.
_NODE_TYPES: tuple[type, ...] = tuple(_node_types(LayoutNode))


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
    from squid_layouts.runtime.component import Component

    return isinstance(value, Component)


def _children(values: tuple[ChildLike, ...], origin: str) -> tuple[LayoutNode, ...]:
    collected: list[LayoutNode] = []
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


def _collect[ItemT](
    values: Iterable[Conditional[ItemT]], kinds: tuple[type[ItemT], ...], origin: str
) -> tuple[ItemT, ...]:
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


def group(*children: ChildLike) -> Group:
    """Related content with no layout opinion; lowers to its children in place."""
    return Group(_children(children, "sl.group()"))


def stack(*children: ChildLike) -> Stack:
    """Content read top to bottom."""
    return Stack(_children(children, "sl.stack()"))


def cluster(*children: ChildLike) -> Cluster:
    """Content read as a set rather than a sequence."""
    return Cluster(_children(children, "sl.cluster()"))


def themed(palette: Palette, *children: ChildLike) -> Themed:
    """Apply a presentation palette to one semantic subtree."""
    return Themed(_children(children, "sl.themed()"), palette)


def section(
    *children: ChildLike,
    heading: TextValue | None = None,
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Section:
    """A titled block of related content; ``accent`` is a house-colour override."""
    return Section(_children(children, "sl.section()"), _opt_text(heading), accent, thumbnail)


def article(
    *children: ChildLike,
    heading: TextValue | None = None,
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Article:
    """A self-contained block that stands on its own."""
    return Article(_children(children, "sl.article()"), _opt_text(heading), accent, thumbnail)


def aside(*children: ChildLike, tone: Tone = Tone.NEUTRAL) -> Aside:
    """Tangential or advisory content, coloured by tone."""
    return Aside(_children(children, "sl.aside()"), tone)


def controlled[ValueT, EventT](
    value: ValueT,
    on_change: Callable[[EventT], Awaitable[None]],
) -> Controlled[ValueT, EventT]:
    """Own this node's value yourself: it wins every render and ``on_change`` gets the writes."""
    return Controlled(value, on_change)


def managed[ValueT](initial: ValueT) -> Managed[ValueT]:
    """Let the engine own this node's value, seeded once with ``initial``."""
    return Managed(initial)


def details(
    *children: ChildLike,
    key: str,
    summary: TextValue,
    open: DisclosureOwnership = CLOSED,
) -> Details:
    """Content the reader expands; ``key`` carries its disclosure state."""
    return Details(key, _text(summary), _children(children, "sl.details()"), open)


def form(
    spec: FormLike,
    *,
    key: str,
    label: TextValue = "Open form",
    on_submit: SubmitHandler | None = None,
    policy: ActionPolicy | None = None,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    guard: Guard | None = None,
) -> FormTrigger:
    """A content control that presents a portable form.

    ``guard`` gates the press that opens the form; the submission that follows completes an
    already-admitted press and is not checked again.
    """
    resolved, handler, default_policy = bind_form(spec, on_submit)
    return FormTrigger(key, _text(label), resolved, handler, policy or default_policy, tone, emphasis, guard)


def item(*children: ChildLike, key: str, label: TextValue, summary: TextValue | None = None) -> Item:
    """One entry of an `items` collection."""
    return Item(key, _text(label), _children(children, "sl.item()"), _opt_text(summary))


def items(
    *entries: Conditional[Item],
    key: str,
    opened: ItemOwnership = UNOPENED,
    display: ItemDisplay = ItemDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Items:
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


def progress(value: float, *, label: TextValue | None = None, maximum: float = 1.0) -> Progress:
    """Completion of a known-length task."""
    return Progress(value, _opt_text(label), maximum)


def measure(value: int | float | str, label: TextValue, *, unit: str | None = None) -> Measure:
    """A single labelled quantity."""
    return Measure(value, _text(label), unit)


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


def column(heading: TextValue, *, key: str = "", importance: Importance = Importance.NORMAL) -> Column:
    """One column of a `table`."""
    return Column(key, _text(heading), importance)


def table_row(*cells: TextValue, key: str = "") -> TableRow:
    """One row of a `table`; cells are positional, in column order."""
    return TableRow(key, tuple(_text(cell) for cell in cells))


def table(
    *rows: Conditional[TableRow],
    columns: Iterable[Conditional[Column]],
    key: str,
    display: TableDisplay = TableDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Table:
    """Tabular data; ``key`` carries the chosen representation and page."""
    return Table(
        _collect(columns, (Column,), "sl.table(columns=)"),
        _collect(rows, (TableRow,), "sl.table()"),
        key,
        display,
        flexibility,
    )


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


def action(
    label: TextValue,
    on_trigger: Callable[[ActionEvent], Awaitable[None]],
    *,
    key: str,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
    allow_grouping: bool | None = None,
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE,
    guard: Guard | None = None,
    feedback: Feedback | None = None,
    record: History | None = None,
) -> Action:
    """A control that runs ``on_trigger``; ``key`` namespaces its custom id.

    ``guard`` decides whether a press may execute now; ``available`` decides whether the
    control is offered at all. A cooldown wants both.

    ``record`` opens the entry for this press, under ``label``, before the handler runs: an
    action whose whole delta is component state needs no `History.record` of its own. One
    that touched the world still calls it, for the ``undo=`` only the handler can write --
    and doing so under ``record=`` raises `HistoryError` rather than making two entries.
    """
    if record is not None and policy is ActionPolicy.PARALLEL_READ:
        message = "a parallel-read action changes nothing, so it has nothing to record"
        raise ValueError(message)
    return Action(
        key, _text(label), on_trigger, tone, emphasis, available, allow_grouping, policy, guard, feedback, record
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


def routed_action(
    label: TextValue,
    route_id: str,
    *,
    key: str,
    tone: Tone = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
) -> RoutedAction:
    """A control the router dispatches, surviving the process that drew it.

    Build ``route_id`` with a `squid_layouts.routing.Route` rather than by hand, so an id
    over Discord's budget fails here and not at send time.
    """
    return RoutedAction(key, _text(label), route_id, tone, emphasis, available)


def action_group(
    *entries: Conditional[Action | Link | RoutedAction], key: str, label: TextValue | None = None
) -> ActionGroup:
    """Controls that belong together and degrade together."""
    return ActionGroup(key, _collect(entries, (Action, Link, RoutedAction), "sl.action_group()"), _opt_text(label))


def actions(
    *entries: Conditional[Action | Link | RoutedAction | ActionGroup],
    key: str,
    display: ActionDisplay = ActionDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Actions:
    """The controls offered by a view; ``key`` carries the chosen presentation."""
    return Actions(
        _collect(entries, (Action, Link, RoutedAction, ActionGroup), "sl.actions()"), key, display, flexibility
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
    channel_types: tuple[ChannelType, ...] = (),
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
        channel_types,
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
    if isinstance(value, Managed):
        selection: ChoiceOwnership = Managed(() if value.initial is None else (str(value.initial),))
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


def destination(label: TextValue, *, key: str, available: bool = True) -> Destination:
    """One place `navigation` can go."""
    return Destination(key, _text(label), available)


def navigation(
    *entries: Conditional[Destination],
    key: str,
    current: NavOwnership = FIRST_DESTINATION,
    display: NavigationDisplay = NavigationDisplay.AUTO,
    flexibility: Flexibility = Flexibility.STABLE,
) -> Navigation:
    """Movement between the views of one message."""
    return Navigation(
        key,
        _collect(entries, (Destination,), "sl.navigation()"),
        current,
        display,
        flexibility,
    )
