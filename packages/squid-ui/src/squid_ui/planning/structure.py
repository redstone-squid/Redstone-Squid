"""Resolve authored palette and control state for semantic occurrences."""

from squid_ui.authoring.semantic import Controlled, Details, ItemDisplay, Items, Navigation, Toggle, Uncontrolled
from squid_ui.palette import Accent, AccentDefault, AccentRole, Palette, Role, Tone
from squid_ui.presentation import PresentationState
from squid_ui.primitives.styles import Color


def resolve_accent(accent: Accent, palette: Palette) -> Color | None:
    """Resolve a structural accent to an exact colour: the palette brand when inherited."""
    if accent is AccentDefault.INHERIT or accent is AccentRole.BRAND:
        return palette.brand
    if isinstance(accent, Tone | Role):
        return palette.tone(accent)
    return accent


def disclosure_state[RenderTargetT, ContextT](
    node: Details[RenderTargetT, ContextT], session: PresentationState
) -> bool:
    """Whether a disclosure is open: the author's word when controlled, session memory otherwise."""
    match node.open:
        case Controlled(value=value):
            return value
        case Uncontrolled(initial=initial):
            return session.disclosure(node.key, initial=initial).open


def toggle_state[ContextT](node: Toggle[ContextT], session: PresentationState) -> bool:
    """Whether a toggle is on: the author's word when controlled, session memory otherwise."""
    match node.on:
        case Controlled(value=value):
            return value
        case Uncontrolled(initial=initial):
            return session.toggle(node.key, initial=initial).on


def item_state[RenderTargetT, ContextT](
    node: Items[RenderTargetT, ContextT], session: PresentationState
) -> tuple[str | None, bool]:
    """The opened entry key and whether it is fixed, validated against the entries that exist.

    A remembered key whose entry has since disappeared is the engine's own stale data, so it
    reads as nothing opened; a controlled value is validated the same way.
    """
    keys = {item.key for item in node.items}
    match node.opened:
        case Controlled(value=value):
            return (value if value in keys else None), True
        case Uncontrolled(initial=initial):
            seed = () if initial is None else (initial,)
            remembered = session.selection(node.key, initial=seed).selected
            opened = remembered[0] if remembered and remembered[0] in keys else None
            return opened, node.key in session.selections or initial is not None


def opened_item[RenderTargetT, ContextT](
    node: Items[RenderTargetT, ContextT], session: PresentationState
) -> str | None:
    """Apply an initial opened display only before the reader or owner has selected a state."""
    opened, fixed = item_state(node, session)
    if opened is None and not fixed and node.display == ItemDisplay.OPENED and node.items:
        return node.items[0].key
    return opened


def navigation_current[ContextT](node: Navigation[ContextT], session: PresentationState) -> str | None:
    """The current destination, defaulting to the first available one.

    A remembered destination that has since gone unavailable is the engine's own stale
    data, so drop it. An author's controlled value is theirs to be wrong about.
    """
    available = tuple(destination for destination in node.options if destination.available)
    match node.current:
        case Controlled(value=value):
            current = value
        case Uncontrolled(initial=initial):
            keys = {destination.key for destination in available}
            seed = () if initial is None else (initial,)
            remembered = session.selection(node.key, initial=seed).selected
            current = remembered[0] if remembered and remembered[0] in keys else None
    if current is None and available:
        current = available[0].key
    return current


__all__ = [
    "disclosure_state",
    "item_state",
    "navigation_current",
    "resolve_accent",
    "toggle_state",
]
