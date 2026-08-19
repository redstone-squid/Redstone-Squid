"""Lower semantic author intent into finite target-shaped strategy candidates."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from squid_layouts.actions import ActionBinding, ActionEvent, PressEvent, SelectionEvent
from squid_layouts.chrome import Chrome
from squid_layouts.constraints import Drop, Never, Overflow, Paginate, Spill, Truncate
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.ir import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.ir import (
    Button,
    Fold,
    Footer,
    Gallery,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    Row,
    SelectMenu,
    Text,
)
from squid_layouts.ir import (
    Code as PrimitiveCode,
)
from squid_layouts.ir import (
    Heading as PrimitiveHeading,
)
from squid_layouts.limits import V2Limits
from squid_layouts.presentation import PresentationSession
from squid_layouts.scene import PlanEvent, PlanSeverity, ScenePager
from squid_layouts.search import StrategyCandidate, choose_strategy
from squid_layouts.semantic import (
    Action,
    ActionDisplay,
    ActionGroup,
    Actions,
    Article,
    Aside,
    BestEffort,
    ChoiceEvent,
    Choices,
    Cluster,
    Code,
    Details,
    FallbackContent,
    Fields,
    Figure,
    Group,
    Heading,
    ItemDisplay,
    Items,
    LayoutNode,
    List,
    Measure,
    Media,
    NavigateEvent,
    Navigation,
    NavigationDisplay,
    OptionalContent,
    Paragraph,
    Progress,
    Quote,
    Section,
    Spilled,
    Stack,
    Status,
    Table,
    TableDisplay,
    Tone,
    Truncated,
)
from squid_layouts.styles import ActionStyle
from squid_layouts.text import resolve_text

type PageState = Mapping[str, int] | int | None
type PageNav = Callable[[str, int, int], Sequence[Node]]

ACTIONS_ADAPTER_ID = "discord.actions"
ACTIONS_ADAPTER_VERSION = 1


@dataclass(frozen=True, slots=True)
class SemanticLowering:
    nodes: tuple[Node, ...]
    events: tuple[PlanEvent, ...] = ()
    pagers: tuple[ScenePager, ...] = ()
    states_explored: int = 0


@dataclass(slots=True)
class _Context:
    limits: V2Limits
    chrome: Chrome
    session: PresentationSession
    page: PageState
    nav: PageNav | None
    events: list[PlanEvent]
    pagers: list[ScenePager]
    states_explored: int = 0


def lower_semantics(
    nodes: Sequence[LayoutNode],
    *,
    limits: V2Limits,
    chrome: Chrome,
    session: PresentationSession,
    page: PageState = None,
    nav: PageNav | None = None,
) -> SemanticLowering:
    """Lower semantic nodes before the existing exact solver measures the result."""
    context = _Context(limits, chrome, session, page, nav, [], [])
    lowered: list[Node] = []
    for index, node in enumerate(nodes):
        lowered.extend(_node(node, f"$.{index}", context))
    return SemanticLowering(tuple(lowered), tuple(context.events), tuple(context.pagers), context.states_explored)


def _node(node: LayoutNode, path: str, context: _Context) -> list[Node]:
    match node:
        case Truncated(node=child, keep=keep):
            return [_with_overflow(item, Truncate(keep)) for item in _node(child, path, context)]
        case Spilled(node=child):
            return [_with_overflow(item, Spill()) for item in _node(child, path, context)]
        case OptionalContent(node=child):
            return [_with_overflow(item, Drop()) for item in _node(child, path, context)]
        case BestEffort(node=child):
            policy: Overflow = Spill() if isinstance(child, List | Fields) else Truncate()
            return [_with_overflow(item, policy) for item in _node(child, path, context)]
        case FallbackContent(primary=primary, alternate=alternate):
            return [
                Fold(
                    _single(_node(primary, f"{path}.primary", context)),
                    _single(_node(alternate, f"{path}.alternate", context)),
                )
            ]
        case Actions():
            return _actions(node, path, context)
        case Group(children=children) | Stack(children=children) | Cluster(children=children):
            return _children(children, path, context)
        case Section(children=children, heading=heading) | Article(children=children, heading=heading):
            contents: list[Node] = []
            if heading is not None:
                contents.append(PrimitiveHeading(resolve_text(heading).content, overflow=Never()))
            contents.extend(_children(children, path, context))
            return [Panel(tuple(contents))]
        case Aside(children=children, tone=tone):
            return [Panel(tuple(_children(children, path, context)), accent=_tone_color(tone))]
        case Heading(content=content, level=level):
            return [PrimitiveHeading(resolve_text(content).content, level=level, overflow=Never())]
        case Paragraph(content=content):
            return [Text(resolve_text(content).content, overflow=Never())]
        case List(items=items, key=key, ordered=ordered, page_size=page_size):
            marker = (lambda index: f"{index + 1}.") if ordered else (lambda _index: "•")
            lines = tuple(f"{marker(index)} {resolve_text(item.content).content}" for index, item in enumerate(items))
            return [Lines(lines, overflow=Paginate(key=key, per=page_size))]
        case Fields(fields=fields):
            return [
                Lines(
                    tuple(
                        f"**{resolve_text(field.label).content}:** {resolve_text(field.value).content}"
                        for field in fields
                    ),
                    overflow=Never(),
                )
            ]
        case Quote(content=content, attribution=attribution):
            value = "> " + resolve_text(content).content.replace("\n", "\n> ")
            if attribution is not None:
                value += f"\n— {resolve_text(attribution).content}"
            return [Text(value, overflow=Never())]
        case Code(content=content, language=language):
            return [PrimitiveCode(content, language, overflow=Never())]
        case Figure(media=media, caption=caption):
            children: list[Node] = [Gallery((media.url,))]
            if caption is not None:
                children.append(Footer(resolve_text(caption).content))
            return children
        case Media():
            return _media(node, context)
        case Details():
            return _details(node, path, context)
        case Status(content=content, tone=tone):
            prefix = {
                Tone.INFO: "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} ",
                Tone.SUCCESS: "✅ ",
                Tone.WARNING: "⚠️ ",
                Tone.DANGER: "❌ ",
            }.get(tone, "")
            return [Text(prefix + resolve_text(content).content, overflow=Never())]
        case Progress(value=value, maximum=maximum, label=label):
            ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
            filled = round(ratio * 10)
            prefix = f"{resolve_text(label).content}: " if label is not None else ""
            return [Text(f"{prefix}{'█' * filled}{'░' * (10 - filled)} {ratio:.0%}", overflow=Never())]
        case Measure(value=value, label=label, unit=unit):
            suffix = f" {unit}" if unit else ""
            return [Text(f"**{resolve_text(label).content}:** {value}{suffix}", overflow=Never())]
        case Choices():
            return _choices(node, path, context)
        case Items():
            return _items(node, path, context)
        case Navigation():
            return _navigation(node, context)
        case Table():
            return _table(node, context)
        case Panel(children=children, accent=accent):
            return [Panel(tuple(_children(children, path, context)), accent)]
        case _:
            return [node]


def _children(children: Sequence[LayoutNode], path: str, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for index, child in enumerate(children):
        lowered.extend(_node(child, f"{path}.{index}", context))
    return lowered


def _single(nodes: Sequence[Node]) -> Node:
    return nodes[0] if len(nodes) == 1 else Panel(tuple(nodes))


def _with_overflow(node: Node, overflow: Overflow) -> Node:
    if isinstance(node, Text | PrimitiveHeading | Footer | PrimitiveCode | Lines):
        return replace(node, overflow=overflow)
    if isinstance(node, Panel):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    return node


def _choices(node: Choices, path: str, context: _Context) -> list[Node]:
    available = tuple(choice for choice in node.choices if choice.available)
    previous = tuple(node.selected)
    if node.maximum == 1 and 2 <= len(available) <= 5:
        buttons: list[Button] = []
        for choice in available:

            async def choose(event: PressEvent, key: str = choice.key) -> None:
                await node.on_change(
                    ChoiceEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        (key,),
                        () if key in previous else (key,),
                        tuple(value for value in previous if value != key),
                    )
                )

            buttons.append(
                Button(
                    resolve_text(choice.label).content,
                    choose,
                    f"{node.key}.{choice.key}",
                    style=ActionStyle.PRIMARY if choice.key in previous else ActionStyle.SECONDARY,
                )
            )
        return [PrimitiveActionGroup(tuple(buttons))]
    if len(available) > context.limits.select_options:
        message = f"{path}: Choices has {len(available)} options; split the choice set or use Items"
        raise LayoutInvariantError(message)

    async def choose_values(event: SelectionEvent) -> None:
        selected = tuple(event.values)
        await node.on_change(
            ChoiceEvent(
                event.actor,
                event.responder,
                event.locale,
                event.context,
                selected,
                tuple(key for key in selected if key not in previous),
                tuple(key for key in previous if key not in selected),
            )
        )

    options = tuple(
        Option(
            resolve_text(choice.label).content,
            choice.key,
            resolve_text(choice.description).content if choice.description is not None else None,
            choice.key in previous,
        )
        for choice in available
    )
    return [
        SelectMenu(
            options,
            choose_values,
            node.key,
            min_values=node.minimum,
            max_values=min(node.maximum, len(options)),
        )
    ]


def _items(node: Items, path: str, context: _Context) -> list[Node]:
    keys = {item.key for item in node.items}
    remembered = context.session.selection(node.key).selected
    focused = (
        node.focused if node.focused in keys else (remembered[0] if remembered and remembered[0] in keys else None)
    )
    if node.display is ItemDisplay.FOCUSED and focused is None and node.items:
        focused = node.items[0].key
    if focused is not None:
        item = next(item for item in node.items if item.key == focused)

        async def back(event: PressEvent) -> None:
            await event.acknowledge()
            context.session.select(node.key, ())
            event.invalidate()

        return [
            PrimitiveHeading(resolve_text(item.label).content, level=3, overflow=Never()),
            *_children(item.children, f"{path}.{item.key}", context),
            Row((Button(context.chrome.back, back, f"{node.key}.back"),)),
        ]

    async def focus(event: SelectionEvent) -> None:
        await event.acknowledge()
        context.session.select(node.key, tuple(event.values[:1]))
        event.invalidate()

    summaries = tuple(
        f"**{resolve_text(item.label).content}**"
        + (f" — {resolve_text(item.summary).content}" if item.summary is not None else "")
        for item in node.items
    )
    if len(node.items) > context.limits.select_options:
        message = f"{path}: Items has {len(node.items)} entries; local item pagination is required"
        raise LayoutInvariantError(message)
    return [
        Lines(summaries, overflow=Never()),
        SelectMenu(
            tuple(Option(resolve_text(item.label).content, item.key) for item in node.items),
            focus,
            f"{node.key}.focus",
            placeholder="Choose an item",
        ),
    ]


def _navigation(node: Navigation, context: _Context) -> list[Node]:
    available = tuple(destination for destination in node.destinations if destination.available)
    grouped = node.display is NavigationDisplay.GROUPED or (
        node.display is NavigationDisplay.AUTO and len(available) > 5
    )

    async def navigate(event: ActionEvent, destination: str) -> None:
        await node.on_navigate(NavigateEvent(event.actor, event.responder, event.locale, event.context, destination))

    if grouped:

        async def select_destination(event: SelectionEvent) -> None:
            if event.values:
                await navigate(event, event.values[0])

        return [
            SelectMenu(
                tuple(
                    Option(
                        resolve_text(destination.label).content,
                        destination.key,
                        default=destination.key == node.current,
                    )
                    for destination in available
                ),
                select_destination,
                node.key,
            )
        ]
    buttons: list[Button] = []
    for destination in available:

        async def go(event: PressEvent, key: str = destination.key) -> None:
            await navigate(event, key)

        buttons.append(
            Button(
                resolve_text(destination.label).content,
                go,
                f"{node.key}.{destination.key}",
                style=ActionStyle.PRIMARY if destination.key == node.current else ActionStyle.SECONDARY,
            )
        )
    return [PrimitiveActionGroup(tuple(buttons))]


def _details(node: Details, path: str, context: _Context) -> list[Node]:
    open_ = context.session.disclosure(node.key, initial=node.open).open

    async def toggle(event: PressEvent) -> None:
        await event.acknowledge()
        current = context.session.disclosure(node.key, initial=node.open).open
        context.session.disclose(node.key, not current)
        event.invalidate()

    result: list[Node] = [Row((Button(resolve_text(node.summary).content, toggle, f"{node.key}.toggle"),))]
    if open_:
        result.extend(_children(node.children, path, context))
    return result


def _table(node: Table, context: _Context) -> list[Node]:
    strategy = context.session.strategy(node.key, "discord.table", 1)
    if strategy is None:
        strategy = "tabular" if node.display is not TableDisplay.RECORDS and len(node.columns) <= 4 else "records"
        context.session.remember_strategy(node.key, "discord.table", 1, strategy)
    if strategy == "tabular":
        headings = [resolve_text(column.heading).content for column in node.columns]
        widths = [
            max([len(heading), *(len(resolve_text(row.cells[index]).content) for row in node.rows)])
            for index, heading in enumerate(headings)
        ]
        lines = [" | ".join(heading.ljust(widths[index]) for index, heading in enumerate(headings))]
        lines.append("-+-".join("-" * width for width in widths))
        lines.extend(
            " | ".join(resolve_text(cell).content.ljust(widths[index]) for index, cell in enumerate(row.cells))
            for row in node.rows
        )
        return [PrimitiveCode("\n".join(lines), overflow=Never())]
    records = tuple(
        "\n".join(
            f"**{resolve_text(column.heading).content}:** {resolve_text(cell).content}"
            for column, cell in zip(node.columns, row.cells, strict=True)
        )
        for row in node.rows
    )
    return [Lines(records, join="\n\n", overflow=Paginate(key=node.key))]


def _media(node: Media, context: _Context) -> list[Node]:
    if not node.items:
        return []
    strategy = context.session.strategy(node.key, "discord.media", 1)
    if strategy is None:
        strategy = "featured" if node.display.value == "featured" else "collection"
        context.session.remember_strategy(node.key, "discord.media", 1, strategy)
    if strategy == "featured":
        first = node.items[0]
        result: list[Node] = [Gallery((first.url,))]
        if first.description is not None:
            result.append(Footer(resolve_text(first.description).content, overflow=Never()))
        return result
    return [
        Gallery(tuple(item.url for item in node.items[start : start + context.limits.gallery_items]))
        for start in range(0, len(node.items), context.limits.gallery_items)
    ]


def _actions(node: Actions, path: str, context: _Context) -> list[Node]:
    strategy = _action_strategy(node, context)
    context.session.remember_strategy(node.key, ACTIONS_ADAPTER_ID, ACTIONS_ADAPTER_VERSION, strategy)
    groups: list[tuple[str, tuple[Action, ...], str | None]] = []
    direct: list[Action | LinkButton] = []
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
                    direct.append(LinkButton(resolve_text(action.label).content, action.url))
            groups.append((item.key, tuple(group_actions), resolve_text(item.label).content if item.label else None))
        elif isinstance(item, Action):
            implicit.append(item)
        else:
            flush_implicit()
            direct.append(LinkButton(resolve_text(item.label).content, item.url))
    flush_implicit()

    result: list[Node] = []
    if strategy == "individual":
        for group_key, actions, _label in groups:
            result.extend(_individual(actions, f"{node.key}.{group_key}", context))
    else:
        for group_key, actions, label in groups:
            result.extend(_grouped(actions, f"{node.key}.{group_key}", label, path, context))
    if direct:
        controls = tuple(_button(action) if isinstance(action, Action) else action for action in direct)
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


def _action_strategy(node: Actions, context: _Context) -> str:
    actions = [action for item in node.items for action in _contained_actions(item)]
    forced_pager = any(
        len(tuple(_contained_actions(item))) > 75 for item in node.items if isinstance(item, ActionGroup)
    )
    if not any(isinstance(item, ActionGroup) for item in node.items):
        forced_pager = len(actions) > 75
    available = ["grouped", "paged"] if forced_pager else ["individual", "grouped"]
    individual_components = len(actions) + (len(actions) + context.limits.row_buttons - 1) // context.limits.row_buttons
    if individual_components > context.limits.total_components and "individual" in available:
        available.remove("individual")
    preferred = {
        ActionDisplay.INDIVIDUAL: "individual",
        ActionDisplay.GROUPED: "grouped",
        ActionDisplay.AUTO: "individual" if len(actions) <= 5 else "grouped",
    }[node.display]
    if forced_pager:
        preferred = "paged"
    baseline = context.session.strategy(node.key, ACTIONS_ADAPTER_ID, ACTIONS_ADAPTER_VERSION)
    order = {"individual": 0, "grouped": 1, "paged": 2}
    choice = choose_strategy(
        tuple(
            StrategyCandidate(
                strategy,
                active_pagers=int(strategy == "paged"),
                transition_distance=abs(order[strategy] - order.get(baseline or preferred, order[strategy])),
            )
            for strategy in available
        ),
        path=f"actions:{node.key}",
        flexibility=node.flexibility,
        preferred=preferred,
        baseline=baseline if baseline in available else None,
    )
    context.states_explored += choice.states_explored
    return choice.candidate.strategy_id


def _contained_actions(item: Action | object) -> Sequence[Action]:
    if isinstance(item, Action):
        return (item,)
    if isinstance(item, ActionGroup):
        return tuple(action for action in item.actions if isinstance(action, Action))
    return ()


def _individual(actions: Sequence[Action], key: str, context: _Context) -> list[Node]:
    controls = tuple(_button(action) for action in actions)
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
            _picker(tuple(eligible[start : start + context.limits.select_options]), f"{key}.{start // 25}", label)
            for start in range(0, len(eligible), context.limits.select_options)
        )
    if direct:
        result.extend(_individual(direct, f"{key}.direct", context))
    return result


def _paged_picker(actions: Sequence[Action], key: str, label: str | None, context: _Context) -> list[Node]:
    per = context.limits.select_options
    pages = (len(actions) + per - 1) // per
    cursor = context.session.cursor(key)
    index = cursor.index
    keys = [action.key for action in actions]
    if cursor.anchor in keys:
        index = keys.index(cursor.anchor) // per
    if isinstance(context.page, Mapping):
        index = context.page.get(key, index)
    index = max(0, min(index, pages - 1))
    chunk = tuple(actions[index * per : (index + 1) * per])
    anchor = chunk[0].key if chunk else None
    context.session.anchor_cursor(key, index, anchor, extent=pages)
    fingerprint = hashlib.blake2s("\0".join(keys).encode(), digest_size=16).hexdigest()
    context.pagers.append(ScenePager(key, index, pages, fingerprint))
    result: list[Node] = [_picker(chunk, f"{key}.page", label)]
    result.append(Footer(context.chrome.page_footer(index + 1, pages)))
    if context.nav is not None:
        result.extend(context.nav(key, index, pages))
    return result


def _picker(actions: Sequence[Action], key: str, label: str | None) -> SelectMenu:
    routes = {action.key: ActionBinding(action.key, action.on_trigger, action.policy) for action in actions}

    async def route(event: SelectionEvent) -> None:
        binding = routes.get(event.values[0]) if len(event.values) == 1 else None
        if binding is not None:
            await binding.handler(event)

    return SelectMenu(
        tuple(Option(resolve_text(action.label).content, action.key) for action in actions),
        route,
        key,
        placeholder=label or "Choose an action",
        routes=routes,
    )


def _button(action: Action) -> Button:
    style = {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(action.tone, ActionStyle.PRIMARY if action.emphasis.value == "strong" else ActionStyle.SECONDARY)
    return Button(
        resolve_text(action.label).content,
        action.on_trigger,
        action.key,
        style=style,
        disabled=not action.available,
        policy=action.policy,
    )


def _tone_color(tone: Tone) -> int | None:
    return {
        Tone.INFO: 0x5865F2,
        Tone.SUCCESS: 0x248046,
        Tone.WARNING: 0xF0B232,
        Tone.DANGER: 0xDA373C,
    }.get(tone)
