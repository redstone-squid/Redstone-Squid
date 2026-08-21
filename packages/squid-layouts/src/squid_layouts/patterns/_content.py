"""Shared content handling for high-level component patterns."""

import re
from collections.abc import Iterable, Mapping

from squid_layouts.factories import _NODE_TYPES
from squid_layouts.runtime.component import Component
from squid_layouts.semantic import LayoutNode, Paragraph
from squid_layouts.text import Message, ResolvedText, TextLike

type ContentItem = LayoutNode | Component
type ContentLike = ContentItem | TextLike | Iterable[ContentItem | TextLike]


def normalize_content(value: object, *, name: str) -> tuple[ContentItem, ...]:
    """Normalize one pattern content slot into renderable nodes and child components."""
    if isinstance(value, Component):
        return (value,)
    if isinstance(value, str | ResolvedText | Message):
        return (Paragraph(value),)
    if isinstance(value, _NODE_TYPES):
        return (value,)
    if isinstance(value, Mapping):
        message = f"{name} cannot be a mapping; unpack the content you meant"
        raise TypeError(message)
    if isinstance(value, Iterable):
        normalized: list[ContentItem] = []
        for index, item in enumerate(value):
            if isinstance(item, Component):
                normalized.append(item)
            elif isinstance(item, str | ResolvedText | Message):
                normalized.append(Paragraph(item))
            elif isinstance(item, _NODE_TYPES):
                normalized.append(item)
            else:
                message = f"{name}[{index}] is not a layout node, text value, or Component"
                raise TypeError(message)
        return tuple(normalized)
    message = f"{name} must be a layout node, text value, Component, or iterable of those"
    raise TypeError(message)


def render_content(owner: Component, content: Iterable[ContentItem], *, prefix: str) -> list[LayoutNode]:
    """Expand component children under stable embed keys while retaining semantic nodes."""
    rendered: list[LayoutNode] = []
    for index, item in enumerate(content):
        rendered.append(owner.embed(item, key=f"{prefix}-{index}") if isinstance(item, Component) else item)
    return rendered


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
