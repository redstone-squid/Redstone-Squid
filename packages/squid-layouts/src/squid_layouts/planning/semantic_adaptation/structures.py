"""Lower tables, grids, rosters, media, and action structures."""

from collections.abc import Sequence

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.grids import GridCell
from squid_layouts.interactions import ActionBinding, PressEvent, SelectionEvent
from squid_layouts.planning.semantic_adaptation.common import (
    _button_style,
    _page_items,
    _resolve,
    _select_strategy,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    ACTIONS_ADAPTER_VERSION,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    action_axis as _action_axis,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    grid_axis as _grid_axis,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    media_axis as _media_axis,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    table_axis as _table_axis,
)
from squid_layouts.planning.semantic_adaptation.model import (
    LoweringContext as _Context,
)
from squid_layouts.planning.semantic_adaptation.regions import (
    _cards,
)
from squid_layouts.primitives.constraints import (
    Never,
    Paginate,
    Spill,
)
from squid_layouts.primitives.nodes import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.primitives.nodes import (
    Button,
    Footer,
    Gallery,
    GalleryItem,
    Lines,
    LinkButton,
    Node,
    Option,
    RoutedButton,
    Row,
    SelectMenu,
    Text,
)
from squid_layouts.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_layouts.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_layouts.scene.model import PlanEvent, PlanSeverity
from squid_layouts.semantic import (
    Action,
    ActionGroup,
    Actions,
    Emphasis,
    Grid,
    Link,
    Media,
    Roster,
    RoutedAction,
    Table,
    Tone,
)
from squid_layouts.sources import Position


def _table(node: Table, path: str, context: _Context) -> list[Node]:
    columns = node.columns.columns
    strategy = _select_strategy(_table_axis(node, path, context.session), context)
    if strategy in {"matrix", "tabular"}:
        headings = [_resolve(column.heading, context) for column in columns]
        widths = [
            max([len(heading), *(len(_resolve(row.cells[index], context)) for row in node.rows)])
            for index, heading in enumerate(headings)
        ]
        separator = "  " if strategy == "matrix" else " | "
        lines = [separator.join(heading.ljust(widths[index]) for index, heading in enumerate(headings))]
        if strategy == "tabular":
            lines.append("-+-".join("-" * width for width in widths))
        lines.extend(
            separator.join(_resolve(cell, context).ljust(widths[index]) for index, cell in enumerate(row.cells))
            for row in node.rows
        )
        return [PrimitiveCode("\n".join(lines), overflow=Never())]
    records = tuple(
        "\n".join(
            f"**{_resolve(column.heading, context)}:** {_resolve(cell, context)}"
            for column, cell in zip(columns, row.cells, strict=True)
        )
        for row in node.rows
    )
    return [Lines(records, join="\n\n", overflow=Paginate(key=node.key))]


def _column_name(index: int) -> str:
    """Return a zero-based spreadsheet column name."""
    name = ""
    cursor = index + 1
    while cursor:
        cursor, remainder = divmod(cursor - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _coordinate(index: int, columns: int) -> str:
    return f"{_column_name(index % columns)}{index // columns + 1}"


def _grid_matrix(node: Grid, context: _Context) -> PrimitiveCode:
    labels = [f"{'[ ]' if cell.available else '[x]'} {_resolve(cell.label, context)}" for cell in node.cells]
    column_names = [_column_name(index) for index in range(node.columns)]
    widths = [
        max(
            [
                len(column_names[column]),
                *(len(labels[index]) for index in range(column, len(labels), node.columns)),
            ]
        )
        for column in range(node.columns)
    ]
    row_label_width = len(str((len(node.cells) + node.columns - 1) // node.columns))
    lines = [
        " " * (row_label_width + 2) + "  ".join(name.ljust(widths[index]) for index, name in enumerate(column_names))
    ]
    for start in range(0, len(labels), node.columns):
        row = labels[start : start + node.columns]
        lines.append(
            f"{start // node.columns + 1:>{row_label_width}}  "
            + "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )
    return PrimitiveCode("\n".join(lines), overflow=Never())


def _grid_option(index: int, cell: GridCell, node: Grid, context: _Context) -> Option:
    return Option(f"{_coordinate(index, node.columns)} ??{_resolve(cell.label, context)}", cell.key)


def _grid_select(cells: Sequence[tuple[int, GridCell]], node: Grid, key: str, context: _Context) -> SelectMenu:
    return SelectMenu(
        tuple(_grid_option(index, cell, node, context) for index, cell in cells),
        node.on_pick,
        key,
        placeholder="Choose a position",
    )


def _grid(node: Grid, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_grid_axis(node, path, context.limits, context.session), context)
    if strategy == "buttons":
        rows: list[Node] = []
        for start in range(0, len(node.cells), node.columns):
            buttons: list[Button] = []
            for cell in node.cells[start : start + node.columns]:

                async def pick(event: PressEvent, cell_key: str = cell.key) -> None:
                    await node.on_pick(
                        SelectionEvent(event.actor, event.responder, event.locale, event.context, (cell_key,))
                    )

                buttons.append(
                    Button(
                        _resolve(cell.label, context),
                        pick,
                        f"{node.key}.{cell.key}",
                        style=_button_style(cell.tone, Emphasis.NORMAL),
                        disabled=not cell.available,
                    )
                )
            rows.append(Row(tuple(buttons)))
        return rows

    available = tuple((index, cell) for index, cell in enumerate(node.cells) if cell.available)
    if strategy == "coordinate":
        result: list[Node] = [_grid_matrix(node, context)]
        if available:
            result.append(_grid_select(available, node, f"{node.key}.coordinate", context))
        return result

    visible, index, pages = _page_items(available, f"{node.key}.cells", context, identity=lambda item: item[1].key)
    return [
        _grid_select(visible, node, f"{node.key}.page", context),
        *context.pages.controls(f"{node.key}.cells", Position(offset=index), pages),
    ]


def _roster(node: Roster, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for group in node.placement.groups:
        slot = group.slot
        count = _resolve(context.chrome.slot_count(len(group.members), slot.capacity), context)
        lowered.append(PrimitiveHeading(f"{_resolve(slot.label, context)} — {count}", level=3, overflow=Never()))
        if group.members:
            lowered.append(
                Lines(tuple(f"- {_resolve(member.display, context)}" for member in group.members), overflow=Spill())
            )
        full = slot.capacity is not None and len(group.members) >= slot.capacity
        available = not node.locked and not (full and node.placement.rejects_overflow)
        if full and node.placement.rejects_overflow:
            lowered.append(Text(_resolve(context.chrome.full, context), overflow=Never()))
        if node.on_join is not None:

            async def join(event: PressEvent, slot_key: str = slot.key) -> None:
                await node.on_join(
                    SelectionEvent(event.actor, event.responder, event.locale, event.context, (slot_key,))
                )

            lowered.append(
                Row(
                    (
                        Button(
                            _resolve(slot.label, context),
                            join,
                            f"{node.key}.{slot.key}",
                            style=_button_style(slot.tone, Emphasis.NORMAL),
                            disabled=not available,
                        ),
                    )
                )
            )
        elif node.routes is not None:
            lowered.append(
                Row(
                    (
                        RoutedButton(
                            _resolve(slot.label, context),
                            node.routes[slot.key],
                            style=_button_style(slot.tone, Emphasis.NORMAL),
                            disabled=not available,
                        ),
                    )
                )
            )
    if node.show_waitlist and node.placement.waitlist:
        lowered.extend(
            (
                PrimitiveHeading(_resolve(context.chrome.waitlist, context), level=3, overflow=Never()),
                Lines(
                    tuple(f"- {_resolve(entry.display, context)}" for entry in node.placement.waitlist),
                    overflow=Spill(),
                ),
            )
        )
    return lowered


def _media(node: Media, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_media_axis(node, path, context.session), context)
    if not node.items:
        return []
    if strategy == "featured":
        first = node.items[0]
        if first.spoiler and _cards(context):
            message = f"{path}: classic targets cannot preserve media spoilers; provide an explicit Variants fallback"
            raise LayoutInvariantError(message)
        result: list[Node] = [Gallery((GalleryItem(first.url, first.description, first.spoiler),))]
        if first.description is not None:
            result.append(Footer(_resolve(first.description, context), overflow=Never()))
        return result
    if _cards(context) and any(item.spoiler for item in node.items):
        message = f"{path}: classic targets cannot preserve media spoilers; provide an explicit Variants fallback"
        raise LayoutInvariantError(message)
    return [
        Gallery(
            tuple(
                GalleryItem(item.url, item.description, item.spoiler)
                for item in node.items[start : start + context.limits.gallery_items]
            )
        )
        for start in range(0, len(node.items), context.limits.gallery_items)
    ]


def _actions(node: Actions, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_action_axis(node, path, context.limits, context.session), context)
    groups: list[tuple[str, tuple[Action, ...], str | None]] = []
    # Links and routed controls carry no binding, so they can never be folded into a select
    # menu the way a group of session actions can: they stay individual buttons.
    direct: list[Action | LinkButton | RoutedButton] = []
    implicit: list[Action] = []

    def flush_implicit() -> None:
        if implicit:
            groups.append(("default", tuple(implicit), None))
            implicit.clear()

    for item in node.items:
        if isinstance(item, ActionGroup):
            flush_implicit()
            group_actions: list[Action] = []
            for action in item.actions:
                if isinstance(action, Action):
                    group_actions.append(action)
                else:
                    direct.append(_unbound_button(action, context))
            groups.append((item.key, tuple(group_actions), _resolve(item.label, context) if item.label else None))
        elif isinstance(item, Action):
            implicit.append(item)
        else:
            flush_implicit()
            direct.append(_unbound_button(item, context))
    flush_implicit()

    result: list[Node] = []
    if strategy == "individual":
        for group_key, actions, _label in groups:
            result.extend(_individual(actions, f"{node.key}.{group_key}", context))
    else:
        for group_key, actions, label in groups:
            result.extend(_grouped(actions, f"{node.key}.{group_key}", label, path, context))
    if direct:
        controls = tuple(_button(action, context) if isinstance(action, Action) else action for action in direct)
        result.append(PrimitiveActionGroup(controls))
    context.events.append(
        PlanEvent(
            code=f"actions.{strategy}",
            path=path,
            message=f"Actions {node.key!r} uses the {strategy} strategy",
            severity=PlanSeverity.ADAPTATION,
            after={"adapter_version": ACTIONS_ADAPTER_VERSION},
        )
    )
    return result


def _individual(actions: Sequence[Action], key: str, context: _Context) -> list[Node]:
    controls = tuple(_button(action, context) for action in actions)
    return [PrimitiveActionGroup(controls)] if controls else []


def _grouped(actions: Sequence[Action], key: str, label: str | None, path: str, context: _Context) -> list[Node]:
    eligible: list[Action] = []
    direct: list[Action] = []
    for action in actions:
        default_grouping = action.emphasis.value != "strong" and action.tone in {Tone.NEUTRAL, Tone.INFO}
        if action.allow_grouping if action.allow_grouping is not None else default_grouping:
            eligible.append(action)
        else:
            direct.append(action)

    result: list[Node] = []
    if len(eligible) > 75:
        result.extend(_paged_picker(eligible, key, label, context))
    else:
        result.extend(
            _picker(
                tuple(eligible[start : start + context.limits.select_options]),
                f"{key}.{start // 25}",
                label,
                context,
            )
            for start in range(0, len(eligible), context.limits.select_options)
        )
    if direct:
        result.extend(_individual(direct, f"{key}.direct", context))
    return result


def _paged_picker(actions: Sequence[Action], key: str, label: str | None, context: _Context) -> list[Node]:
    chunk, index, pages = _page_items(actions, key, context, identity=lambda action: action.key)
    return [
        _picker(chunk, f"{key}.page", label, context),
        *context.pages.controls(key, Position(offset=index), pages),
    ]


def _picker(actions: Sequence[Action], key: str, label: str | None, context: _Context) -> SelectMenu:
    routes = {
        action.key: ActionBinding(
            action.key,
            action.on_trigger,
            action.policy,
            guard=action.guard,
            label=_resolve(action.label, context),
            record=action.record,
        )
        for action in actions
    }

    async def route(event: SelectionEvent) -> None:
        binding = routes.get(event.values[0]) if len(event.values) == 1 else None
        if binding is not None:
            await binding.handler(event)

    return SelectMenu(
        tuple(Option(_resolve(action.label, context), action.key) for action in actions),
        route,
        key,
        placeholder=label or "Choose an action",
        routes=routes,
    )


def _unbound_button(item: Link | RoutedAction, context: _Context) -> LinkButton | RoutedButton:
    """Lower a control the mount never dispatches: a URL, or a router's own custom id."""
    label = _resolve(item.label, context)
    if isinstance(item, Link):
        return LinkButton(label, item.url)
    return RoutedButton(
        label, item.route_id, style=_button_style(item.tone, item.emphasis), disabled=not item.available
    )


def _button(action: Action, context: _Context) -> Button:
    return Button(
        _resolve(action.label, context),
        action.on_trigger,
        action.key,
        style=_button_style(action.tone, action.emphasis),
        disabled=not action.available,
        policy=action.policy,
        guard=action.guard,
        feedback=action.feedback,
        record=action.record,
    )
