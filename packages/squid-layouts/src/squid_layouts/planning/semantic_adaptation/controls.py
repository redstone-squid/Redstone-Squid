"""Lower forms, choices, entities, navigation, and stateful controls."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from squid_layouts.capabilities import Capability
from squid_layouts.entity import EntityKind, EntityRef
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormBinding
from squid_layouts.interactions import ActionEvent, EntitySelectionEvent, PressEvent, SelectionEvent
from squid_layouts.planning.semantic_adaptation.common import (
    _button_style,
    _page_items,
    _resolve,
    _select_strategy,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    item_state as _item_state,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    items_axis as _items_axis,
)
from squid_layouts.planning.semantic_adaptation.decisions import (
    navigation_axis as _navigation_axis,
)
from squid_layouts.planning.semantic_adaptation.model import (
    LoweringContext as _Context,
)
from squid_layouts.primitives.constraints import (
    Never,
    Overflow,
)
from squid_layouts.primitives.nodes import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.primitives.nodes import (
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
from squid_layouts.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_layouts.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.semantic import (
    Choice,
    ChoiceEvent,
    Choices,
    Controlled,
    Details,
    Emphasis,
    Entities,
    EntityEvent,
    FormTrigger,
    Items,
    LayoutNode,
    Managed,
    NavigateEvent,
    Navigation,
    OpenEvent,
    RoutedChoices,
    Toggle,
    ToggleEvent,
)
from squid_layouts.sources import Position


def _form(node: FormTrigger, context: _Context) -> list[Node]:
    if Capability.FORMS_MODAL not in context.capabilities:
        message = "target does not support forms"
        raise LayoutInvariantError(message)
    spec = node.spec.adapt(context.capabilities, maximum_fields=context.limits.components.modal_components)

    async def present(event: PressEvent) -> None:
        await event.present_form(
            spec,
            key=node.key,
            on_submit=node.on_submit,
            policy=node.policy,
            label=node.label,
            record=node.record,
        )

    return [
        PrimitiveActionGroup(
            (
                FormButton(
                    _resolve(node.label, context),
                    present,
                    node.key,
                    style=_button_style(node.tone, node.emphasis),
                    policy=node.policy,
                    # Guarding the press that opens the modal, not the submission: a stateful
                    # guard checked twice would deny the reader's own filled-in form.
                    guard=node.guard,
                    # The adapted spec, not `node.spec`: it is what the reader will actually
                    # be shown, and so what a late submission must be parsed against.
                    form=FormBinding(node.key, spec, node.on_submit, node.policy, node.label, node.record),
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
                    _resolve(choice.label, context),
                    choose,
                    f"{node.key}.{choice.key}",
                    style=ActionStyle.PRIMARY if choice.key in previous else ActionStyle.SECONDARY,
                )
            )
        return [PrimitiveActionGroup(tuple(buttons))]
    page_key = f"{node.key}.choices"
    if len(available) > context.limits.components.select_options and node.maximum != 1:
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
            choose_values,
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
        case Managed(initial=initial):
            initial_keys = tuple(_entity_key(value) for value in initial)
            stored = context.session.selection(node.key, initial=initial_keys).selected
            previous = tuple(_entity_ref(key) for key in stored)

    async def commit(event: ActionEvent, selected: tuple[EntityRef, ...]) -> None:
        match node.selection:
            case Controlled(on_change=on_change):
                await on_change(
                    EntityEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        selected,
                        tuple(value for value in selected if value not in previous),
                        tuple(value for value in previous if value not in selected),
                    )
                )
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, tuple(_entity_key(value) for value in selected))
                event.invalidate()

    if Capability.ACTIONS_DISCORD_ENTITY in context.capabilities:

        async def select_entities(event: EntitySelectionEvent) -> None:
            await commit(event, event.values)

        return [
            EntitySelect(
                node.entity_type,
                select_entities,
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

    async def choose_fallback(event: ChoiceEvent) -> None:
        await commit(event, tuple(by_key[key] for key in event.selected if key in by_key))

    fallback = Choices(
        key=node.key,
        choices=tuple(
            Choice(_entity_key(choice.ref), choice.label, choice.description, choice.available)
            for choice in node.choices
        ),
        selection=Controlled(tuple(_entity_key(value) for value in previous), choose_fallback),
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
            PrimitiveHeading(_resolve(item.label.content, context), level=3, overflow=Never()),
            *lower_children(item.children, f"{path}.{item.key}", context),
            Row((Button(context.chrome.back, back, f"{node.key}.back"),)),
        ]

    async def focus(event: SelectionEvent) -> None:
        await open_(event, event.values[0] if event.values else None)

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
            focus,
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
        case Managed(initial=initial):
            # A remembered destination that has since gone unavailable is the engine's own
            # stale data, so drop it. An author's value is theirs to be wrong about.
            keys = {destination.key for destination in available}
            seed = () if initial is None else (initial,)
            remembered = context.session.selection(node.key, initial=seed).selected
            current = remembered[0] if remembered and remembered[0] in keys else None
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
                        _resolve(destination.label, context),
                        destination.key,
                        default=destination.key == current,
                    )
                    for destination in visible
                ),
                select_destination,
                node.key,
            )
        ]
        result.extend(context.pages.controls(page_key, Position(offset=page), pages))
        return result
    buttons: list[Button] = []
    for destination in available:

        async def go(event: PressEvent, key: str = destination.key) -> None:
            await navigate(event, key)

        buttons.append(
            Button(
                _resolve(destination.label, context),
                go,
                f"{node.key}.{destination.key}",
                style=ActionStyle.PRIMARY if destination.key == current else ActionStyle.SECONDARY,
            )
        )
    return [PrimitiveActionGroup(tuple(buttons))]


def _details(
    node: Details,
    path: str,
    context: _Context,
    lower_children: Callable[[Sequence[LayoutNode], str, _Context], list[Node]],
) -> list[Node]:
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

    result: list[Node] = [Row((Button(_resolve(node.summary.content, context), toggle, f"{node.key}.toggle"),))]
    if open_:
        result.extend(lower_children(node.children, path, context))
    return result


def _toggle(node: Toggle, context: _Context) -> list[Node]:
    match node.on:
        case Controlled(value=value):
            on = value
        case Managed(initial=initial):
            on = context.session.toggle(node.key, initial=initial).on

    async def flip(event: PressEvent) -> None:
        match node.on:
            case Controlled(on_change=on_change):
                await on_change(ToggleEvent(event.actor, event.responder, event.locale, event.context, not on))
            case Managed(initial=initial):
                await event.acknowledge()
                current = context.session.toggle(node.key, initial=initial).on
                context.session.set_toggle(node.key, on=not current)
                event.invalidate()

    state_label = node.on_label if on else node.off_label
    if state_label is None:
        state_label = context.chrome.on if on else context.chrome.off
    label = f"{_resolve(node.label, context)}: {_resolve(state_label, context)}"
    button = Button(
        label,
        flip,
        node.key,
        style=_button_style(node.tone, Emphasis.NORMAL),
        disabled=not node.available,
    )
    return [Row((button,))]
