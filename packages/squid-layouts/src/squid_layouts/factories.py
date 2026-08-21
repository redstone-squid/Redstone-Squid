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
from string.templatelib import Template
from types import UnionType
from typing import Literal, NoReturn, TypeAliasType, get_args

from squid_layouts.actions import ActionEvent, ActionPolicy
from squid_layouts.primitives.styles import Color
from squid_layouts.semantic import (
    Action,
    ActionDisplay,
    ActionGroup,
    Actions,
    Article,
    Aside,
    Choice,
    ChoiceEvent,
    Choices,
    Cluster,
    Code,
    Column,
    Destination,
    Details,
    Emphasis,
    Field,
    Fields,
    Figure,
    Flexibility,
    Group,
    Heading,
    Importance,
    Item,
    ItemDisplay,
    Items,
    LayoutNode,
    Link,
    List,
    ListItem,
    Measure,
    Media,
    MediaDisplay,
    MediaItem,
    NavigateEvent,
    Navigation,
    NavigationDisplay,
    Note,
    Paragraph,
    Progress,
    Quote,
    Section,
    Stack,
    Status,
    Table,
    TableDisplay,
    TableRow,
    Tone,
)
from squid_layouts.text import ResolvedText, TextLike, md

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
        detail = "components are placed with self.embed(child, key=...)"
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


def section(
    *children: ChildLike,
    heading: TextValue | None = None,
    accent: Color | None = None,
    thumbnail: str | None = None,
) -> Section:
    """A titled block of related content; ``accent`` is a house-colour override."""
    return Section(_children(children, "sl.section()"), _opt_text(heading), accent, thumbnail)


def article(
    *children: ChildLike,
    heading: TextValue | None = None,
    accent: Color | None = None,
    thumbnail: str | None = None,
) -> Article:
    """A self-contained block that stands on its own."""
    return Article(_children(children, "sl.article()"), _opt_text(heading), accent, thumbnail)


def aside(*children: ChildLike, tone: Tone = Tone.NEUTRAL) -> Aside:
    """Tangential or advisory content, coloured by tone."""
    return Aside(_children(children, "sl.aside()"), tone)


def details(*children: ChildLike, key: str, summary: TextValue, open: bool = False) -> Details:
    """Content the reader expands; ``key`` carries its disclosure state."""
    return Details(key, _text(summary), _children(children, "sl.details()"), open)


def item(*children: ChildLike, key: str, label: TextValue, summary: TextValue | None = None) -> Item:
    """One entry of an `items` collection."""
    return Item(key, _text(label), _children(children, "sl.item()"), _opt_text(summary))


def items(
    *entries: Conditional[Item],
    key: str,
    focused: str | None = None,
    display: ItemDisplay = ItemDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Items:
    """A set of entries the reader browses; ``key`` carries the focused entry."""
    return Items(key, _collect(entries, (Item,), "sl.items()"), focused, display, flexibility)


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
) -> Action:
    """A control that runs ``on_trigger``; ``key`` namespaces its custom id."""
    return Action(key, _text(label), on_trigger, tone, emphasis, available, allow_grouping, policy)


def link(label: TextValue, url: str, *, key: str, emphasis: Emphasis = Emphasis.NORMAL) -> Link:
    """A control that opens ``url``."""
    return Link(key, _text(label), url, emphasis)


def action_group(*entries: Conditional[Action | Link], key: str, label: TextValue | None = None) -> ActionGroup:
    """Controls that belong together and degrade together."""
    return ActionGroup(key, _collect(entries, (Action, Link), "sl.action_group()"), _opt_text(label))


def actions(
    *entries: Conditional[Action | Link | ActionGroup],
    key: str,
    display: ActionDisplay = ActionDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Actions:
    """The controls offered by a view; ``key`` carries the chosen presentation."""
    return Actions(_collect(entries, (Action, Link, ActionGroup), "sl.actions()"), key, display, flexibility)


def choice(label: TextValue, *, key: str, description: TextValue | None = None, available: bool = True) -> Choice:
    """One option of a `choices` control; ``key`` is the value it submits."""
    return Choice(key, _text(label), _opt_text(description), available)


def choices(
    *entries: Conditional[Choice],
    key: str,
    selected: Iterable[str],
    on_change: Callable[[ChoiceEvent], Awaitable[None]],
    minimum: int = 1,
    maximum: int = 1,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Choices:
    """A control that selects among options; ``key`` namespaces its custom id."""
    return Choices(
        key,
        _collect(entries, (Choice,), "sl.choices()"),
        tuple(selected),
        on_change,
        minimum,
        maximum,
        flexibility,
    )


def destination(label: TextValue, *, key: str, available: bool = True) -> Destination:
    """One place `navigation` can go."""
    return Destination(key, _text(label), available)


def navigation(
    *entries: Conditional[Destination],
    key: str,
    current: str,
    on_navigate: Callable[[NavigateEvent], Awaitable[None]],
    display: NavigationDisplay = NavigationDisplay.AUTO,
    flexibility: Flexibility = Flexibility.STABLE,
) -> Navigation:
    """Movement between the views of one message."""
    return Navigation(
        key,
        _collect(entries, (Destination,), "sl.navigation()"),
        current,
        on_navigate,
        display,
        flexibility,
    )
