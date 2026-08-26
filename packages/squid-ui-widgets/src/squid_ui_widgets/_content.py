"""Shared content handling for high-level component patterns."""

import re
from collections.abc import Iterable, Mapping

from squid_ui.factories import is_layout_node, paragraph
from squid_ui.runtime.component import Component
from squid_ui.semantic import LayoutNode
from squid_ui.text import Message, ResolvedText, TextLike

type ContentItem = LayoutNode | Component
type ContentLike = ContentItem | TextLike | Iterable[ContentItem | TextLike]


def normalize_content(value: object, *, name: str) -> tuple[ContentItem, ...]:
    """Normalize one pattern content slot into renderable nodes and child components."""
    if isinstance(value, Component):
        return (value,)
    if isinstance(value, str | ResolvedText | Message):
        return (paragraph(value),)
    if is_layout_node(value):
        return (value,)
    if isinstance(value, Mapping):
        message = f"{name} cannot be a mapping; unpack the content you meant"
        raise TypeError(message)
    if isinstance(value, Iterable):
        return tuple(_normalize_item(item, name=f"{name}[{index}]") for index, item in enumerate(value))
    message = f"{name} must be a layout node, text value, Component, or iterable of those"
    raise TypeError(message)


def _normalize_item(value: object, *, name: str) -> ContentItem:
    """Normalize one member of an iterable content slot."""
    if isinstance(value, Component):
        return value
    if isinstance(value, str | ResolvedText | Message):
        return paragraph(value)
    if is_layout_node(value):
        return value
    message = f"{name} is not a layout node, text value, or Component"
    raise TypeError(message)


def render_content(owner: Component, content: Iterable[ContentItem], *, prefix: str) -> tuple[LayoutNode, ...]:
    """Expand component children under stable embed keys while retaining semantic nodes."""
    return tuple(
        owner.boundary(item, key=f"{prefix}-{index}") if isinstance(item, Component) else item
        for index, item in enumerate(content)
    )


def require_key(value: str, *, name: str) -> str:
    """Validate a key used to identify a pattern or one of its destinations."""
    if not value:
        message = f"{name} must not be empty"
        raise ValueError(message)
    return value


def slug(value: TextLike) -> str:
    """Derive a readable fallback key for the short ``MenuEntry(label, content)`` form."""
    if isinstance(value, str):
        source = value
    elif isinstance(value, ResolvedText):
        source = value.content
    else:
        source = value.template
    result = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-")
    return result or "entry"


def display_text(value: object) -> str:
    """Turn a projected ranking value into display text without exposing dataclass reprs."""
    if isinstance(value, str):
        return value
    if isinstance(value, ResolvedText):
        return value.content
    if isinstance(value, Message):
        return value.template
    return str(value)
