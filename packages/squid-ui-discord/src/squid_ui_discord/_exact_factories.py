"""Shared factories for exact Discord layout nodes."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from squid_ui.emoji import EmojiLike
from squid_ui.entity import ConversationType, EntityRef, EntityType
from squid_ui.guards import Guard
from squid_ui.interactions import (
    ActionBinding,
    ActionMode,
    BusySpec,
    EntitySelectionHandler,
    PressHandler,
    SelectionHandler,
)
from squid_ui.primitives.constraints import Alt, Overflow, Spill, Truncate
from squid_ui.primitives.nodes import (
    Button,
    Code,
    ControlGroup,
    EntitySelect,
    Footer,
    Heading,
    Lines,
    LinkButton,
    Option,
    PremiumButton,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Text,
)
from squid_ui.primitives.styles import ActionStyle
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History

type Conditional[ValueT] = ValueT | None | Literal[False]
type Control = LinkButton | PremiumButton | Button | RoutedButton | RawItem


def text(content: TextLike, *, overflow: Overflow | None = None, priority: int = 0) -> Text:
    """Build exact Discord text."""
    return Text(content, overflow=Truncate() if overflow is None else overflow, priority=priority)


def heading(
    content: TextLike,
    *,
    level: int = 2,
    overflow: Overflow | None = None,
    priority: int = 10,
) -> Heading:
    """Build an exact Discord heading."""
    return Heading(content, level=level, overflow=Truncate() if overflow is None else overflow, priority=priority)


def footer(content: TextLike, *, overflow: Overflow | None = None, priority: int = -10) -> Footer:
    """Build exact small footer text."""
    return Footer(content, overflow=Truncate() if overflow is None else overflow, priority=priority)


def code(
    content: TextLike,
    *,
    lang: str = "",
    overflow: Overflow | None = None,
    priority: int = 0,
) -> Code:
    """Build an exact fenced code block."""
    return Code(content, lang=lang, overflow=Truncate() if overflow is None else overflow, priority=priority)


def lines(
    *entries: TextLike | Alt,
    join: str = "\n",
    overflow: Overflow | None = None,
    priority: int = 0,
) -> Lines:
    """Build exact line-oriented text from positional entries."""
    return Lines(tuple(entries), join=join, overflow=Spill() if overflow is None else overflow, priority=priority)


def button(
    label: TextLike | None,
    on_click: PressHandler,
    *,
    key: str,
    style: ActionStyle = ActionStyle.SECONDARY,
    emoji: EmojiLike | None = None,
    disabled: bool = False,
    mode: ActionMode = ActionMode.EXCLUSIVE,
    guard: Guard | None = None,
    busy: BusySpec | None = None,
    record: History | None = None,
) -> Button:
    """Build an exact dispatchable button."""
    return Button(label, on_click, key, style, emoji, disabled, mode, guard, busy, record)


def link_button(
    label: TextLike | None,
    url: str,
    *,
    emoji: EmojiLike | None = None,
    disabled: bool = False,
) -> LinkButton:
    """Build an exact link button."""
    return LinkButton(label, url, emoji, disabled)


def premium_button(sku_id: int) -> PremiumButton:
    """Build an exact premium button."""
    return PremiumButton(sku_id)


def routed_button(
    label: TextLike | None,
    route_id: str,
    *,
    style: ActionStyle = ActionStyle.SECONDARY,
    emoji: EmojiLike | None = None,
    disabled: bool = False,
) -> RoutedButton:
    """Build an exact routed button."""
    return RoutedButton(label, route_id, style, emoji, disabled)


def option(
    label: TextLike,
    value: str,
    *,
    description: TextLike | None = None,
    default: bool = False,
    emoji: EmojiLike | None = None,
) -> Option:
    """Build an exact string-select option."""
    return Option(label, value, description, default, emoji)


def select(
    *options: Option,
    on_select: SelectionHandler,
    key: str,
    placeholder: TextLike | None = None,
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
    mode: ActionMode = ActionMode.EXCLUSIVE,
    routes: Mapping[str, ActionBinding] | None = None,
) -> SelectMenu:
    """Build an exact dispatchable string select."""
    return SelectMenu(
        tuple(options),
        on_select,
        key,
        placeholder,
        min_values,
        max_values,
        disabled,
        mode,
        {} if routes is None else routes,
    )


def entity_select(
    entity_type: EntityType,
    on_select: EntitySelectionHandler,
    *,
    key: str,
    placeholder: TextLike | None = None,
    default_values: tuple[EntityRef, ...] = (),
    conversation_types: tuple[ConversationType, ...] = (),
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
    mode: ActionMode = ActionMode.EXCLUSIVE,
) -> EntitySelect:
    """Build an exact entity select."""
    return EntitySelect(
        entity_type,
        on_select,
        key,
        placeholder,
        default_values,
        conversation_types,
        min_values,
        max_values,
        disabled,
        mode,
    )


def routed_select(
    *options: Option,
    route_id: str,
    placeholder: TextLike | None = None,
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
) -> RoutedSelect:
    """Build an exact routed string select."""
    return RoutedSelect(tuple(options), route_id, placeholder, min_values, max_values, disabled)


def row(*items: Conditional[Control]) -> Row:
    """Build exactly one Discord action row."""
    return Row(_controls(items, "row"))


def controls(*items: Conditional[Control]) -> ControlGroup:
    """Build controls that the planner may arrange across valid rows."""
    return ControlGroup(_controls(items, "controls"))


def _controls(items: tuple[Conditional[Control], ...], origin: str) -> tuple[Control, ...]:
    collected: list[Control] = []
    for index, item in enumerate(items):
        if item is None or item is False:
            continue
        if item is True or not isinstance(item, LinkButton | PremiumButton | Button | RoutedButton | RawItem):
            message = f"{origin} argument {index} is not a Discord button"
            raise TypeError(message)
        collected.append(item)
    return tuple(collected)


__all__ = [
    "Conditional",
    "button",
    "code",
    "controls",
    "entity_select",
    "footer",
    "heading",
    "lines",
    "link_button",
    "option",
    "premium_button",
    "routed_button",
    "routed_select",
    "row",
    "select",
    "text",
]
