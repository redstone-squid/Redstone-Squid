"""Build structured collections, media, and host-owned counts and placements."""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from string.templatelib import Template

from squid_i18n import Text, emoji_text, strong
from squid_ui.authoring.grids import GridCell
from squid_ui.authoring.rosters import RosterPlacement
from squid_ui.authoring.semantic import (
    UNOPENED,
    ChoiceEvent,
    Column,
    Columns,
    Field,
    Fields,
    Flexibility,
    Grid,
    Importance,
    Item,
    ItemDisplay,
    ItemLabel,
    ItemOwnership,
    Items,
    LayoutNode,
    List,
    ListItem,
    Media,
    MediaDisplay,
    MediaItem,
    Roster,
    Stack,
    Table,
    TableDisplay,
    TableRow,
)
from squid_ui.authoring.tallies import TallyOption
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.assets import FileInput
from squid_ui.content.text import TextLike
from squid_ui.factories._inputs import ChildLike, Conditional, TextValue, _children, _collect
from squid_ui.factories.containers import stack
from squid_ui.factories.content import media_item, progress
from squid_ui.factories.controls import choice, choices, controlled, routed_choices
from squid_ui.interactions import SelectionEvent
from squid_ui.overflow import FIRST_PAGE, PageOwnership
from squid_ui.target_types import RenderTarget


def item[RenderTargetT = RenderTarget, ContextT = object](
    label: ItemLabel, *children: ChildLike[RenderTargetT, ContextT], key: str, summary: TextValue | None = None
) -> Item[RenderTargetT, ContextT]:
    """One entry of an `items` collection."""
    summary = None if summary is None else Text(summary)
    return Item(key, label, _children(children, "sl.item()"), summary)


def item_label(content: TextValue) -> ItemLabel:
    """The identity shown for one `items` entry."""
    content = Text(content)
    return ItemLabel(content)


def items[RenderTargetT = RenderTarget, ContextT = object](
    *entries: Conditional[Item[RenderTargetT, ContextT]],
    key: str,
    opened: ItemOwnership[ContextT] = UNOPENED,
    display: ItemDisplay = ItemDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Items[RenderTargetT, ContextT]:
    """A set of entries the reader browses; ``key`` carries the opened entry."""
    return Items(key, _collect(entries, (Item,), "sl.items()"), opened, display, flexibility)


def field(
    label: TextValue,
    value: TextValue,
    *,
    key: str = "",
    importance: Importance = Importance.NORMAL,
    fallbacks: Iterable[TextValue] = (),
) -> Field:
    """One labelled value; ``fallbacks`` are shorter forms tried under budget pressure."""
    label = Text(label)
    value = Text(value)
    fallbacks = tuple(Text(value) for value in fallbacks)
    return Field(key, label, value, importance, tuple(fallbacks))


def fields(*entries: Conditional[Field]) -> Fields:
    """A block of labelled values that never loses a whole field."""
    return Fields(_collect(entries, (Field,), "sl.fields()"))


def bullet(content: TextValue, *, key: str = "", importance: Importance = Importance.NORMAL) -> ListItem:
    """One entry of a `bullets` list."""
    content = Text(content)
    return ListItem(key, content, importance)


def bullets(
    *entries: Conditional[ListItem | TextLike | Template],
    key: str,
    ordered: bool = False,
    page_size: int | None = None,
    group_size: int | None = None,
    page: PageOwnership = FIRST_PAGE,
) -> List:
    """A bulleted or numbered list; bare text becomes an entry. ``key`` carries its page."""
    collected = _collect(entries, (ListItem, str, Text, Template), "sl.bullets()")
    return List(
        tuple(entry if isinstance(entry, ListItem) else bullet(entry) for entry in collected),
        key,
        ordered,
        page_size,
        page,
        group_size=group_size,
    )


def column(heading: TextValue, *, key: str = "") -> Column:
    """One column of a `table`."""
    heading = Text(heading)
    return Column(key, heading)


def columns(*entries: Conditional[Column]) -> Columns:
    """The ordered schema that precedes a `table`'s rows."""
    collected = _collect(entries, (Column,), "sl.columns()")
    if not collected:
        message = "sl.columns() needs at least one column"
        raise ValueError(message)
    return Columns(collected)


def table_row(*cells: TextValue, key: str = "") -> TableRow:
    """One row of a `table`; cells are positional, in column order."""
    cells = tuple(Text(value) for value in cells)
    return TableRow(key, tuple(cells))


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


def _tally_label(option: TallyOption, chrome: Chrome) -> Text:
    label = strong(option.label) if option.mine else option.label
    count = option.count
    result = Text(chrome.tally_option(label, count))
    return Text(t"{emoji_text(option.emoji)} {result}") if option.emoji is not None else result


def tally[ContextT = object](
    options: Sequence[TallyOption],
    *,
    key: str,
    on_vote: Callable[[SelectionEvent[ContextT]], Awaitable[None]] | None = None,
    route_id: str | None = None,
    total: int | None = None,
    show_bars: bool = True,
    chrome: Chrome = DEFAULT_CHROME,
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
    entries = tuple(choice(_tally_label(option, chrome), key=option.key) for option in options)
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
            *(bullet(_tally_label(option, chrome), key=option.key) for option in options),
            key=f"{key}.options",
        )
    return stack(*bars, control)


def media(
    *sources: Conditional[MediaItem | FileInput],
    key: str,
    filename: str | None = None,
    media_type: str | None = None,
    description: TextValue | None = None,
    spoiler: bool = False,
    display: MediaDisplay = MediaDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Media:
    """Preview paths, bytes, binary streams, or URLs, without reading local files yet.

    Bytes and unnamed streams need filename=. Metadata keywords describe a single
    source; for galleries, use media_item() on entries that need metadata.
    """
    description = None if description is None else Text(description)
    entries = tuple(source for source in sources if source is not None and source is not False)
    metadata = filename is not None or media_type is not None or description is not None or spoiler
    if metadata and (len(entries) != 1 or isinstance(entries[0], MediaItem)):
        msg = "media metadata requires one bare source; use media_item() for gallery entries"
        raise ValueError(msg)
    return Media(
        tuple(
            source
            if isinstance(source, MediaItem)
            else media_item(
                source,
                filename=filename,
                media_type=media_type,
                description=description,
                spoiler=spoiler,
            )
            for source in entries
        ),
        key,
        display,
        flexibility,
    )


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
