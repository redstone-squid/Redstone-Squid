"""Lower semantic author intent into finite target-shaped strategy candidates."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from squid_layouts.actions import ActionBinding, SelectionEvent
from squid_layouts.chrome import Chrome
from squid_layouts.constraints import Paginate
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.ir import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.ir import (
    Button,
    Footer,
    Gallery,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
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
from squid_layouts.semantic import (
    Action,
    ActionDisplay,
    ActionGroup,
    Actions,
    Article,
    Aside,
    Choices,
    Cluster,
    Code,
    Details,
    Fields,
    Figure,
    Group,
    Heading,
    Items,
    List,
    Measure,
    Media,
    Navigation,
    Paragraph,
    Progress,
    Quote,
    Section,
    SemanticNode,
    Stack,
    Status,
    Tone,
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


@dataclass(slots=True)
class _Context:
    limits: V2Limits
    chrome: Chrome
    session: PresentationSession
    page: PageState
    nav: PageNav | None
    events: list[PlanEvent]
    pagers: list[ScenePager]


def lower_semantics(
    nodes: Sequence[Node | SemanticNode],
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
    return SemanticLowering(tuple(lowered), tuple(context.events), tuple(context.pagers))


def _node(node: Node | SemanticNode, path: str, context: _Context) -> list[Node]:
    match node:
        case Actions():
            return _actions(node, path, context)
        case Group(children=children) | Stack(children=children) | Cluster(children=children):
            return _children(children, path, context)
        case Section(children=children, heading=heading) | Article(children=children, heading=heading):
            contents: list[Node] = []
            if heading is not None:
                contents.append(PrimitiveHeading(resolve_text(heading).content))
            contents.extend(_children(children, path, context))
            return [Panel(tuple(contents))]
        case Aside(children=children, tone=tone):
            return [Panel(tuple(_children(children, path, context)), accent=_tone_color(tone))]
        case Heading(content=content, level=level):
            return [PrimitiveHeading(resolve_text(content).content, level=level)]
        case Paragraph(content=content):
            return [Text(resolve_text(content).content)]
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
                    )
                )
            ]
        case Quote(content=content, attribution=attribution):
            value = "> " + resolve_text(content).content.replace("\n", "\n> ")
            if attribution is not None:
                value += f"\n— {resolve_text(attribution).content}"
            return [Text(value)]
        case Code(content=content, language=language):
            return [PrimitiveCode(content, language)]
        case Figure(media=media, caption=caption):
            children: list[Node] = [Gallery((media.url,))]
            if caption is not None:
                children.append(Footer(resolve_text(caption).content))
            return children
        case Media(items=items):
            return [Gallery(tuple(item.url for item in items[: context.limits.gallery_items]))]
        case Details(summary=summary, children=children, open=open_):
            result: list[Node] = [PrimitiveHeading(resolve_text(summary).content, level=3)]
            if open_:
                result.extend(_children(children, path, context))
            return result
        case Status(content=content, tone=tone):
            prefix = {
                Tone.INFO: "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} ",
                Tone.SUCCESS: "✅ ",
                Tone.WARNING: "⚠️ ",
                Tone.DANGER: "❌ ",
            }.get(tone, "")
            return [Text(prefix + resolve_text(content).content)]
        case Progress(value=value, maximum=maximum, label=label):
            ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
            filled = round(ratio * 10)
            prefix = f"{resolve_text(label).content}: " if label is not None else ""
            return [Text(f"{prefix}{'█' * filled}{'░' * (10 - filled)} {ratio:.0%}")]
        case Measure(value=value, label=label, unit=unit):
            suffix = f" {unit}" if unit else ""
            return [Text(f"**{resolve_text(label).content}:** {value}{suffix}")]
        case Choices() | Items() | Navigation():
            message = f"{path}: {type(node).__name__} semantic adapter is not implemented yet"
            raise LayoutInvariantError(message)
        case Panel(children=children, accent=accent):
            return [Panel(tuple(_children(children, path, context)), accent)]
        case _:
            return [node]


def _children(children: Sequence[Node | SemanticNode], path: str, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for index, child in enumerate(children):
        lowered.extend(_node(child, f"{path}.{index}", context))
    return lowered


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
    sticky = context.session.strategy(node.key, ACTIONS_ADAPTER_ID, ACTIONS_ADAPTER_VERSION)
    if sticky in available:
        return sticky
    preferred = {
        ActionDisplay.INDIVIDUAL: "individual",
        ActionDisplay.GROUPED: "grouped",
        ActionDisplay.AUTO: "individual" if len(actions) <= 5 else "grouped",
    }[node.display]
    if forced_pager:
        preferred = "paged"
    return preferred if preferred in available else available[0]


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
