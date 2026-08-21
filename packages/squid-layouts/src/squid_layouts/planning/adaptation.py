"""Lower semantic author intent into finite target-shaped strategy candidates."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from squid_layouts.actions import ActionBinding, ActionEvent, PressEvent, SelectionEvent
from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.cursors import PageBroker, PageRequest, content_fingerprint
from squid_layouts.planning.limits import V2Limits
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET, StrategyCandidate, choose_strategy
from squid_layouts.primitives.constraints import Alt, Condense, Drop, Never, Overflow, Paginate, Spill, Truncate
from squid_layouts.primitives.nodes import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.primitives.nodes import (
    Button,
    Footer,
    Gallery,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    RoutedButton,
    Row,
    SelectMenu,
    Text,
    Thumbnail,
    Variant,
    Variants,
)
from squid_layouts.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_layouts.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_layouts.primitives.nodes import (
    Section as PrimitiveSection,
)
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.runtime.presentation import (
    PresentationSession,
    SessionUpdate,
    StrategyState,
    StrategyUpdate,
)
from squid_layouts.scene.model import PlanEvent, PlanSeverity, ScenePager
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
    Controlled,
    Details,
    Emphasis,
    FallbackContent,
    Field,
    Fields,
    Figure,
    Group,
    Heading,
    ItemDisplay,
    Items,
    LayoutNode,
    Link,
    List,
    Managed,
    Measure,
    Media,
    NavigateEvent,
    Navigation,
    NavigationDisplay,
    Note,
    OpenEvent,
    OptionalContent,
    Paragraph,
    Progress,
    Quote,
    RoutedAction,
    Section,
    Spilled,
    Stack,
    Status,
    Table,
    TableDisplay,
    Tone,
    Truncated,
)
from squid_layouts.text import resolve_text

ACTIONS_ADAPTER_ID = "discord.actions"
ACTIONS_ADAPTER_VERSION = 1


@dataclass(frozen=True, slots=True)
class SemanticLowering:
    nodes: tuple[Node, ...]
    events: tuple[PlanEvent, ...] = ()
    pagers: tuple[ScenePager, ...] = ()
    updates: tuple[SessionUpdate, ...] = ()
    states_explored: int = 0
    search_fallback: bool = False


@dataclass(slots=True)
class _Context:
    limits: V2Limits
    chrome: Chrome
    session: PresentationSession
    pages: PageBroker
    events: list[PlanEvent]
    updates: list[SessionUpdate]
    search_budget: int = DEFAULT_SEARCH_BUDGET
    states_explored: int = 0
    search_fallback: bool = False


def lower_semantics(
    nodes: Sequence[LayoutNode],
    *,
    limits: V2Limits,
    chrome: Chrome,
    session: PresentationSession,
    pages: PageBroker | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> SemanticLowering:
    """Lower semantic nodes before the existing exact solver measures the result."""
    broker = pages if pages is not None else PageBroker(session, chrome)
    context = _Context(limits, chrome, session, broker, [], [], search_budget)
    lowered: list[Node] = []
    for index, node in enumerate(nodes):
        lowered.extend(_node(node, f"$.{index}", context))
    return SemanticLowering(
        tuple(lowered),
        tuple(context.events),
        context.pages.pagers,
        tuple(context.updates),
        context.states_explored,
        context.search_fallback,
    )


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
        case FallbackContent(primary=primary, alternates=alternates):
            rungs = [Variant(tuple(_node(primary, f"{path}.primary", context)))]
            rungs.extend(
                Variant(tuple(_node(alternate, f"{path}.alternate.{index}", context)))
                for index, alternate in enumerate(alternates)
            )
            return [Variants(tuple(rungs))]
        case Actions():
            return _actions(node, path, context)
        case Group(children=children) | Stack(children=children) | Cluster(children=children):
            return _children(children, path, context)
        case (
            Section(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
            | Article(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
        ):
            contents: list[Node] = []
            if heading is not None:
                title = PrimitiveHeading(resolve_text(heading).content, overflow=Never())
                # The lead image sits beside the title and nothing else: picking "the body"
                # out of an arbitrary children tuple would be a guess.
                contents.append(
                    PrimitiveSection(texts=(title,), accessory=Thumbnail(thumbnail)) if thumbnail else title
                )
            elif thumbnail:
                contents.append(Gallery((thumbnail,)))
            contents.extend(_children(children, path, context))
            return [Panel(tuple(contents), accent=accent)]
        case Aside(children=children, tone=tone):
            return [Panel(tuple(_children(children, path, context)), accent=_tone_color(tone))]
        case Heading(content=content, level=level):
            return [PrimitiveHeading(resolve_text(content).content, level=level, overflow=Never())]
        case Paragraph(content=content):
            return [Text(resolve_text(content).content, overflow=Never())]
        case Note(content=content):
            return [Footer(resolve_text(content).content, overflow=Never())]
        case List(items=items, key=key, ordered=ordered, page_size=page_size):
            marker = (lambda index: f"{index + 1}.") if ordered else (lambda _index: "•")
            lines = tuple(f"{marker(index)} {resolve_text(item.content).content}" for index, item in enumerate(items))
            return [Lines(lines, overflow=Paginate(key=key, per=page_size))]
        case Fields(fields=fields):
            return [Lines(tuple(_field_entry(field) for field in fields), overflow=Condense())]
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


def _remember(key: str, adapter_id: str, version: int, strategy: str, context: _Context) -> None:
    """Stage an adapter's sticky choice. Lowering reads the session and writes nothing."""
    context.updates.append(StrategyUpdate(key, StrategyState(key, adapter_id, version, strategy)))


def _field_entry(field: Field) -> str | Alt:
    """One `Fields` line, carrying the field's own degradation ladder when it has one.

    Rungs come from caller data assembled by formatting and escaping, so one that came out
    empty or longer than what precedes it is skipped rather than rejected — direct `Alt`
    construction stays strict.
    """
    label = resolve_text(field.label).content
    primary = f"**{label}:** {resolve_text(field.value).content}"
    kept: list[str] = []
    ceiling = len(primary)
    for fallback in field.fallbacks:
        rung = f"**{label}:** {resolve_text(fallback).content}"
        if len(rung) <= ceiling:
            kept.append(rung)
            ceiling = len(rung)
    return Alt(primary, tuple(kept)) if kept else primary


def _children(children: Sequence[LayoutNode], path: str, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for index, child in enumerate(children):
        lowered.extend(_node(child, f"{path}.{index}", context))
    return lowered


def _with_overflow(node: Node, overflow: Overflow) -> Node:
    if isinstance(node, Text | PrimitiveHeading | Footer | PrimitiveCode | Lines):
        return replace(node, overflow=overflow)
    if isinstance(node, Panel):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    return node


def _choices(node: Choices, path: str, context: _Context) -> list[Node]:
    available = tuple(choice for choice in node.choices if choice.available)
    match node.selection:
        case Controlled(value=value):
            previous = tuple(value)
        case Managed(initial=initial):
            previous = context.session.selection(node.key, initial=tuple(initial)).selected

    async def commit(event: ActionEvent, selected: tuple[str, ...]) -> None:
        match node.selection:
            case Controlled(on_change=on_change):
                await on_change(
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
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, selected)
                event.invalidate()

    if node.maximum == 1 and 2 <= len(available) <= 5:
        buttons: list[Button] = []
        for choice in available:

            async def choose(event: PressEvent, key: str = choice.key) -> None:
                await commit(event, (key,))

            buttons.append(
                Button(
                    resolve_text(choice.label).content,
                    choose,
                    f"{node.key}.{choice.key}",
                    style=ActionStyle.PRIMARY if choice.key in previous else ActionStyle.SECONDARY,
                )
            )
        return [PrimitiveActionGroup(tuple(buttons))]
    page_key = f"{node.key}.choices"
    if len(available) > context.limits.select_options and node.maximum != 1:
        message = (
            f"{path}: Choices has {len(available)} options and selects up to {node.maximum}; "
            "cross-page multi-selection is ambiguous, so group the choices or use Items"
        )
        raise LayoutInvariantError(message)
    visible, page, pages = _page_items(available, page_key, context, identity=lambda choice: choice.key)

    async def choose_values(event: SelectionEvent) -> None:
        await commit(event, tuple(event.values))

    options = tuple(
        Option(
            resolve_text(choice.label).content,
            choice.key,
            resolve_text(choice.description).content if choice.description is not None else None,
            choice.key in previous,
        )
        for choice in visible
    )
    result: list[Node] = [
        SelectMenu(
            options,
            choose_values,
            node.key,
            min_values=node.minimum,
            max_values=min(node.maximum, len(options)),
        )
    ]
    result.extend(context.pages.controls(page_key, page, pages))
    return result


def _items(node: Items, path: str, context: _Context) -> list[Node]:
    # An entry the author named but no longer supplies means the list, not a crash.
    keys = {item.key for item in node.items}
    match node.opened:
        case Controlled(value=value):
            opened = value if value in keys else None
        case Managed(initial=initial):
            seed = () if initial is None else (initial,)
            remembered = context.session.selection(node.key, initial=seed).selected
            opened = remembered[0] if remembered and remembered[0] in keys else None
    if node.display is ItemDisplay.OPENED and opened is None and node.items:
        opened = node.items[0].key

    async def open_(event: ActionEvent, entry: str | None) -> None:
        match node.opened:
            case Controlled(on_change=on_change):
                await on_change(OpenEvent(event.actor, event.responder, event.locale, event.context, opened=entry))
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, () if entry is None else (entry,))
                event.invalidate()

    if opened is not None:
        item = next(item for item in node.items if item.key == opened)

        async def back(event: PressEvent) -> None:
            await open_(event, None)

        return [
            PrimitiveHeading(resolve_text(item.label).content, level=3, overflow=Never()),
            *_children(item.children, f"{path}.{item.key}", context),
            Row((Button(context.chrome.back, back, f"{node.key}.back"),)),
        ]

    async def focus(event: SelectionEvent) -> None:
        await open_(event, event.values[0] if event.values else None)

    page_key = f"{node.key}.items"
    visible, page, pages = _page_items(node.items, page_key, context, identity=lambda item: item.key)
    summaries = tuple(
        f"**{resolve_text(item.label).content}**"
        + (f" — {resolve_text(item.summary).content}" if item.summary is not None else "")
        for item in visible
    )
    result: list[Node] = [
        Lines(summaries, overflow=Never()),
        SelectMenu(
            tuple(Option(resolve_text(item.label).content, item.key) for item in visible),
            focus,
            f"{node.key}.focus",
            placeholder="Choose an item",
        ),
    ]
    result.extend(context.pages.controls(page_key, page, pages))
    return result


def _navigation(node: Navigation, context: _Context) -> list[Node]:
    available = tuple(destination for destination in node.destinations if destination.available)
    grouped = node.display is NavigationDisplay.GROUPED or (
        node.display is NavigationDisplay.AUTO and len(available) > 5
    )

    match node.current:
        case Controlled(value=value):
            current = value
        case Managed(initial=initial):
            seed = () if initial is None else (initial,)
            remembered = context.session.selection(node.key, initial=seed).selected
            current = remembered[0] if remembered else None
    if current is None and available:
        current = available[0].key

    async def navigate(event: ActionEvent, destination: str) -> None:
        match node.current:
            case Controlled(on_change=on_change):
                await on_change(NavigateEvent(event.actor, event.responder, event.locale, event.context, destination))
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, (destination,))
                event.invalidate()

    if grouped:
        page_key = f"{node.key}.destinations"
        visible, page, pages = _page_items(available, page_key, context, identity=lambda item: item.key)

        async def select_destination(event: SelectionEvent) -> None:
            if event.values:
                await navigate(event, event.values[0])

        result: list[Node] = [
            SelectMenu(
                tuple(
                    Option(
                        resolve_text(destination.label).content,
                        destination.key,
                        default=destination.key == current,
                    )
                    for destination in visible
                ),
                select_destination,
                node.key,
            )
        ]
        result.extend(context.pages.controls(page_key, page, pages))
        return result
    buttons: list[Button] = []
    for destination in available:

        async def go(event: PressEvent, key: str = destination.key) -> None:
            await navigate(event, key)

        buttons.append(
            Button(
                resolve_text(destination.label).content,
                go,
                f"{node.key}.{destination.key}",
                style=ActionStyle.PRIMARY if destination.key == current else ActionStyle.SECONDARY,
            )
        )
    return [PrimitiveActionGroup(tuple(buttons))]


def _details(node: Details, path: str, context: _Context) -> list[Node]:
    match node.open:
        case Controlled(value=value):
            open_ = value
        case Managed(initial=initial):
            open_ = context.session.disclosure(node.key, initial=initial).open

    async def toggle(event: PressEvent) -> None:
        match node.open:
            case Controlled(on_change=on_change):
                await on_change(OpenEvent(event.actor, event.responder, event.locale, event.context, opened=not open_))
            case Managed(initial=seed):
                await event.acknowledge()
                context.session.disclose(node.key, not context.session.disclosure(node.key, initial=seed).open)
                event.invalidate()

    result: list[Node] = [Row((Button(resolve_text(node.summary).content, toggle, f"{node.key}.toggle"),))]
    if open_:
        result.extend(_children(node.children, path, context))
    return result


def _table(node: Table, context: _Context) -> list[Node]:
    strategy = context.session.strategy(node.key, "discord.table", 1)
    if strategy is None:
        strategy = "tabular" if node.display is not TableDisplay.RECORDS and len(node.columns) <= 4 else "records"
        _remember(node.key, "discord.table", 1, strategy, context)
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
        _remember(node.key, "discord.media", 1, strategy, context)
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
    _remember(node.key, ACTIONS_ADAPTER_ID, ACTIONS_ADAPTER_VERSION, strategy, context)
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
                    direct.append(_unbound_button(action))
            groups.append((item.key, tuple(group_actions), resolve_text(item.label).content if item.label else None))
        elif isinstance(item, Action):
            implicit.append(item)
        else:
            flush_implicit()
            direct.append(_unbound_button(item))
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
    candidates = tuple(
        StrategyCandidate(
            strategy,
            active_pagers=int(strategy == "paged"),
            transition_distance=abs(order[strategy] - order.get(baseline or preferred, order[strategy])),
        )
        for strategy in available
    )
    if context.states_explored + len(candidates) > context.search_budget:
        selected = baseline if baseline in available else preferred if preferred in available else available[-1]
        context.states_explored += 1
        context.search_fallback = True
        context.events.append(
            PlanEvent(
                code="planner.search_fallback",
                path=f"actions:{node.key}",
                message=(
                    f"Strategy search reached its {context.search_budget}-state budget; "
                    f"selected lossless {selected!r} fallback"
                ),
                severity=PlanSeverity.WARNING,
            )
        )
        return selected
    choice = choose_strategy(
        candidates,
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
    chunk, index, pages = _page_items(actions, key, context, identity=lambda action: action.key)
    return [_picker(chunk, f"{key}.page", label), *context.pages.controls(key, index, pages)]


def _page_items[T](
    items: Sequence[T],
    pager_key: str,
    context: _Context,
    *,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], int, int]:
    """Window a list of options 25 at a time, following the item the reader was on."""
    per = context.limits.select_options
    keys = [identity(item) for item in items]
    anchors: dict[str, int] = {}
    for position, key in enumerate(keys):
        anchors.setdefault(key, position // per)
    request = PageRequest(
        key=pager_key,
        pages=max(1, (len(items) + per - 1) // per),
        fingerprint=content_fingerprint(keys),
        anchors=anchors,
    )
    grant = context.pages.grant(request)
    visible = tuple(items[grant.index * per : (grant.index + 1) * per])
    context.pages.record(request, grant.index, anchor=identity(visible[0]) if visible else None)
    return visible, grant.index, grant.pages


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


def _unbound_button(item: Link | RoutedAction) -> LinkButton | RoutedButton:
    """Lower a control the mount never dispatches: a URL, or a router's own custom id."""
    label = resolve_text(item.label).content
    if isinstance(item, Link):
        return LinkButton(label, item.url)
    return RoutedButton(
        label, item.custom_id, style=_button_style(item.tone, item.emphasis), disabled=not item.available
    )


def _button_style(tone: Tone, emphasis: Emphasis) -> ActionStyle:
    return {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(tone, ActionStyle.PRIMARY if emphasis is Emphasis.STRONG else ActionStyle.SECONDARY)


def _button(action: Action) -> Button:
    return Button(
        resolve_text(action.label).content,
        action.on_trigger,
        action.key,
        style=_button_style(action.tone, action.emphasis),
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
