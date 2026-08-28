"""Lower forms, choices, entities, navigation, and stateful controls."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from squid_ui.capabilities import Capability
from squid_ui.entity import EntityKind, EntityRef
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.planning.semantic_adaptation.common import (
    _button_style,
    _page_items,
    _resolve,
    _select_strategy,
)
from squid_ui.planning.semantic_adaptation.decisions import (
    item_state as _item_state,
)
from squid_ui.planning.semantic_adaptation.decisions import (
    items_axis as _items_axis,
)
from squid_ui.planning.semantic_adaptation.decisions import (
    navigation_axis as _navigation_axis,
)
from squid_ui.planning.semantic_adaptation.handlers import (
    ChoiceCommit,
    ChooseChoice,
    CloseItem,
    EntityCommit,
    FlipToggle,
    FocusItem,
    GoToDestination,
    ItemCommit,
    NavigationCommit,
    PresentForm,
    SelectChoices,
    SelectDestination,
    SelectEntities,
    SelectEntityFallback,
    ToggleDetails,
)
from squid_ui.planning.semantic_adaptation.model import (
    LoweringContext as _Context,
)
from squid_ui.planning.structure import disclosure_state, toggle_action_key, toggle_state
from squid_ui.primitives.constraints import (
    Never,
    Overflow,
)
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    EntitySelect,
    Footer,
    FormButton,
    Lines,
    Node,
    Option,
    Panel,
    RoutedSelect,
    Row,
    SelectMenu,
    Text,
)
from squid_ui.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_ui.primitives.nodes import (
    ControlGroup as PrimitiveActionGroup,
)
from squid_ui.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_ui.primitives.styles import ActionStyle
from squid_ui.semantic import (
    Choice,
    Choices,
    Controlled,
    Details,
    Emphasis,
    Entities,
    FormTrigger,
    Items,
    LayoutNode,
    Navigation,
    RoutedChoices,
    Toggle,
    Uncontrolled,
)
from squid_ui.sources import Position


def _form(node: FormTrigger, context: _Context) -> list[Node]:
    if Capability.FORMS_MODAL not in context.capabilities:
        message = "target does not support forms"
        raise LayoutInvariantError(message)
    spec = node.spec.adapt(context.capabilities, maximum_fields=context.limits.components.modal_components)

    return [
        PrimitiveActionGroup(
            (
                FormButton(
                    _resolve(node.label, context),
                    PresentForm(spec, node.key, node.on_submit, node.mode, node.label, node.record),
                    node.key,
                    style=_button_style(node.tone, node.emphasis),
                    mode=node.mode,
                    # Guarding the press that opens the modal, not the submission: a stateful
                    # guard checked twice would deny the reader's own filled-in form.
                    guard=node.guard,
                    # The adapted spec, not `node.spec`: it is what the reader will actually
                    # be shown, and so what a late submission must be parsed against.
                    form=FormBinding(node.key, spec, node.on_submit, node.mode, node.label, node.record),
                ),
            )
        )
    ]


def _with_overflow(node: Node, overflow: Overflow) -> Node:
    if isinstance(node, Text | PrimitiveHeading | Footer | PrimitiveCode | Lines):
        return replace(node, overflow=overflow)
    if isinstance(node, Panel):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    if isinstance(node, Budget | Break):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    return node


def _with_best_effort(node: Node) -> Node:
    if isinstance(node, Budget):
        return replace(
            node,
            children=tuple(_with_best_effort(child) for child in node.children),
            best_effort=True,
        )
    if isinstance(node, Panel | Break):
        return replace(node, children=tuple(_with_best_effort(child) for child in node.children))
    return node


def _choices(node: Choices, path: str, context: _Context) -> list[Node]:
    available = tuple(choice for choice in node.choices if choice.available)
    match node.selection:
        case Controlled(value=value):
            previous = tuple(value)
        case Uncontrolled(initial=initial):
            previous = context.session.selection(node.key, initial=tuple(initial)).selected

    commit = ChoiceCommit(node.selection, node.key, previous, context.session)

    if node.maximum == 1 and 2 <= len(available) <= 5:
        buttons = [
            Button(
                _resolve(choice.label, context),
                ChooseChoice(commit, choice.key),
                f"{node.key}.{choice.key}",
                style=ActionStyle.PRIMARY if choice.key in previous else ActionStyle.SECONDARY,
            )
            for choice in available
        ]
        return [PrimitiveActionGroup(tuple(buttons))]
    page_key = f"{node.key}.choices"
    if len(available) > context.limits.components.select_options and node.maximum != 1:
        message = (
            f"{path}: Choices has {len(available)} options and selects up to {node.maximum}; "
            "cross-page multi-selection is ambiguous, so group the choices or use Items"
        )
        raise LayoutInvariantError(message)
    visible, page, pages = _page_items(available, page_key, context, identity=lambda choice: choice.key)

    options = tuple(
        Option(
            _resolve(choice.label, context),
            choice.key,
            _resolve(choice.description, context) if choice.description is not None else None,
            choice.key in previous,
        )
        for choice in visible
    )
    result: list[Node] = [
        SelectMenu(
            options,
            SelectChoices(commit),
            node.key,
            min_values=node.minimum,
            max_values=min(node.maximum, len(options)),
        )
    ]
    result.extend(context.pages.controls(page_key, Position(offset=page), pages))
    return result


def _entity_key(ref: EntityRef) -> str:
    return f"{ref.kind.value}:{ref.id}"


def _entity_ref(key: str) -> EntityRef:
    kind, raw_id = key.split(":", 1)
    return EntityRef(EntityKind(kind), int(raw_id))


def _entities(node: Entities, path: str, context: _Context) -> list[Node]:
    match node.selection:
        case Controlled(value=value):
            previous = tuple(value)
        case Uncontrolled(initial=initial):
            initial_keys = tuple(_entity_key(value) for value in initial)
            stored = context.session.selection(node.key, initial=initial_keys).selected
            previous = tuple(_entity_ref(key) for key in stored)

    commit = EntityCommit(node.selection, node.key, previous, context.session)

    if Capability.ACTIONS_DISCORD_ENTITY in context.capabilities:
        return [
            EntitySelect(
                node.entity_type,
                SelectEntities(commit),
                node.key,
                placeholder=_resolve(node.placeholder, context) if node.placeholder is not None else None,
                default_values=previous,
                channel_types=node.channel_types,
                min_values=node.minimum,
                max_values=node.maximum,
            )
        ]
    if not node.choices:
        message = f"{path}: Entities requires actions.discord.entity or enumerated fallback choices"
        raise LayoutInvariantError(message)

    available = tuple(choice for choice in node.choices if choice.available)
    by_key = {_entity_key(choice.ref): choice.ref for choice in available}
    previous = tuple(value for value in previous if _entity_key(value) in by_key)
    commit = EntityCommit(node.selection, node.key, previous, context.session)

    fallback = Choices(
        key=node.key,
        choices=tuple(
            Choice(_entity_key(choice.ref), choice.label, choice.description, choice.available)
            for choice in node.choices
        ),
        selection=Controlled(
            tuple(_entity_key(value) for value in previous),
            SelectEntityFallback(commit, by_key),
        ),
        minimum=node.minimum,
        maximum=node.maximum,
        flexibility=node.flexibility,
    )
    return _choices(fallback, path, context)


def _routed_choices(node: RoutedChoices, path: str, context: _Context) -> list[Node]:
    """Lower an explicitly stateless picker without inventing mount-owned pagination."""
    available = tuple(choice for choice in node.choices if choice.available)
    if not available:
        message = f"{path}: RoutedChoices needs at least one available choice"
        raise LayoutInvariantError(message)
    return [
        RoutedSelect(
            options=tuple(
                Option(
                    _resolve(choice.label, context),
                    choice.key,
                    _resolve(choice.description, context) if choice.description is not None else None,
                )
                for choice in available
            ),
            route_id=node.route_id,
            placeholder=_resolve(node.placeholder, context) if node.placeholder is not None else None,
            min_values=node.minimum,
            max_values=min(node.maximum, len(available)),
            disabled=not node.available,
        )
    ]


def _items(
    node: Items,
    path: str,
    context: _Context,
    lower_children: Callable[[Sequence[LayoutNode], str, _Context], list[Node]],
) -> list[Node]:
    opened, _fixed = _item_state(node, context.session)
    strategy = _select_strategy(_items_axis(node, path, context.limits, context.session), context)
    if strategy == "opened" and opened is None and node.items:
        opened = node.items[0].key
    commit = ItemCommit(node.opened, node.key, context.session)

    if opened is not None:
        item = next(item for item in node.items if item.key == opened)

        return [
            PrimitiveHeading(_resolve(item.label.content, context), level=3, overflow=Never()),
            *lower_children(item.children, f"{path}.{item.key}", context),
            Row((Button(context.chrome.back, CloseItem(commit), f"{node.key}.back"),)),
        ]

    page_key = f"{node.key}.items"
    visible, page, pages = _page_items(node.items, page_key, context, identity=lambda item: item.key)
    summaries = tuple(
        f"**{_resolve(item.label.content, context)}**"
        + (f" — {_resolve(item.summary, context)}" if item.summary is not None else "")
        for item in visible
    )
    result: list[Node] = [
        Lines(summaries, overflow=Never()),
        SelectMenu(
            tuple(Option(_resolve(item.label.content, context), item.key) for item in visible),
            FocusItem(commit),
            f"{node.key}.focus",
            placeholder="Choose an item",
        ),
    ]
    result.extend(context.pages.controls(page_key, Position(offset=page), pages))
    return result


def _navigation(node: Navigation, path: str, context: _Context) -> list[Node]:
    available = tuple(destination for destination in node.options if destination.available)
    strategy = _select_strategy(_navigation_axis(node, path, context.limits, context.session), context)
    grouped = strategy == "grouped"

    match node.current:
        case Controlled(value=value):
            current = value
        case Uncontrolled(initial=initial):
            # A remembered destination that has since gone unavailable is the engine's own
            # stale data, so drop it. An author's value is theirs to be wrong about.
            keys = {destination.key for destination in available}
            seed = () if initial is None else (initial,)
            remembered = context.session.selection(node.key, initial=seed).selected
            current = remembered[0] if remembered and remembered[0] in keys else None
    if current is None and available:
        current = available[0].key
    commit = NavigationCommit(node.current, node.key, context.session)

    if grouped:
        page_key = f"{node.key}.destinations"
        visible, page, pages = _page_items(available, page_key, context, identity=lambda item: item.key)

        result: list[Node] = [
            SelectMenu(
                tuple(
                    Option(
                        _resolve(destination.label, context),
                        destination.key,
                        default=destination.key == current,
                    )
                    for destination in visible
                ),
                SelectDestination(commit),
                node.key,
            )
        ]
        result.extend(context.pages.controls(page_key, Position(offset=page), pages))
        return result
    buttons = [
        Button(
            _resolve(destination.label, context),
            GoToDestination(commit, destination.key),
            f"{node.key}.{destination.key}",
            style=ActionStyle.PRIMARY if destination.key == current else ActionStyle.SECONDARY,
        )
        for destination in available
    ]
    return [PrimitiveActionGroup(tuple(buttons))]


def _details(
    node: Details,
    path: str,
    context: _Context,
    lower_children: Callable[[Sequence[LayoutNode], str, _Context], list[Node]],
) -> list[Node]:
    open_ = disclosure_state(node, context.session)

    result: list[Node] = [
        Row(
            (
                Button(
                    _resolve(node.summary.content, context),
                    ToggleDetails(node, open_, context.session),
                    toggle_action_key(node.key),
                ),
            )
        )
    ]
    if open_:
        result.extend(lower_children(node.children, path, context))
    return result


def _toggle(node: Toggle, context: _Context) -> list[Node]:
    on = toggle_state(node, context.session)

    state_label = node.on_label if on else node.off_label
    if state_label is None:
        state_label = context.chrome.on if on else context.chrome.off
    label = f"{_resolve(node.label, context)}: {_resolve(state_label, context)}"
    button = Button(
        label,
        FlipToggle(node, on, context.session),
        node.key,
        style=_button_style(node.tone, Emphasis.NORMAL),
        disabled=not node.available,
    )
    return [Row((button,))]
