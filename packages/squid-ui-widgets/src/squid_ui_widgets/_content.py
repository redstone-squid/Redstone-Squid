"""Shared content handling for high-level component machines."""

import re
from collections.abc import Iterable, Mapping

from squid_ui.factories import is_layout_node, paragraph
from squid_ui.runtime.component import Component
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import RenderTarget
from squid_ui.text import Message, ResolvedText, TextLike, source_text

type ContentItem[RenderTargetT: RenderTarget = RenderTarget, ContextT = object] = (
    LayoutNode[RenderTargetT, ContextT] | Component[RenderTargetT, ContextT]
)
type ContentLike[RenderTargetT: RenderTarget = RenderTarget, ContextT = object] = (
    ContentItem[RenderTargetT, ContextT] | TextLike | Iterable[ContentItem[RenderTargetT, ContextT] | TextLike]
)
"""Text, layout nodes, components, or a flat iterable of them.

The render-target type survives normalization: a V2-only child makes its containing widget
V2-only as well, so embedding it cannot silently erase that restriction.
"""


def normalize_content[RenderTargetT: RenderTarget, ContextT = object](
    value: ContentLike[RenderTargetT, ContextT], *, name: str
) -> tuple[ContentItem[RenderTargetT, ContextT], ...]:
    """Normalize text, nodes, components, or a flat iterable into a tuple.

    Iterables are consumed immediately. Mappings and nested iterables are rejected.
    """
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


def _normalize_item[RenderTargetT: RenderTarget, ContextT = object](
    value: ContentItem[RenderTargetT, ContextT] | TextLike, *, name: str
) -> ContentItem[RenderTargetT, ContextT]:
    """Normalize one member of an iterable content slot."""
    if isinstance(value, Component):
        return value
    if isinstance(value, str | ResolvedText | Message):
        return paragraph(value)
    if is_layout_node(value):
        return value
    message = f"{name} is not a layout node, text value, or Component"
    raise TypeError(message)


def render_content[RenderTargetT: RenderTarget, ContextT = object](
    content: Iterable[ContentItem[RenderTargetT, ContextT]], *, prefix: str
) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
    """Embed component children under stable boundary keys and pass layout nodes through."""
    return tuple(
        item.keyed(f"{prefix}-{index}") if isinstance(item, Component) else item for index, item in enumerate(content)
    )


def require_key(value: str, *, name: str) -> str:
    """Validate a key used to identify a machine or one of its destinations."""
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
        source = value.declaration.identifier if value.declaration is not None else source_text(value)
    result = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-")
    return result or "entry"


def display_text(value: object) -> str:
    """Render text wrappers directly, falling back to str for other values."""
    if isinstance(value, str):
        return value
    if isinstance(value, ResolvedText):
        return value.content
    if isinstance(value, Message):
        return source_text(value)
    return str(value)
