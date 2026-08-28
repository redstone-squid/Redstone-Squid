"""The structural rules every semantic traversal states identically, stated once.

The HTML compiler, the Discord lowering pass, and the decision nomination walk all visit the
same semantic tree, and each had grown its own copy of the grammar that names a position in
it. A spelling corrected in one walk stayed wrong in the others, and the planner's
path-keyed state only holds together while every walk agrees on the spelling.

What belongs here is target-neutral by definition: how a child's path is derived from its
parent's, how a fallback branch is named and selected, how a stateful node's current state
is read from author control or session memory, and how a control's action key is spelled.
How a target reacts to what it finds there does not.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

from squid_ui.palette import Accent, AccentDefault, Palette
from squid_ui.primitives.styles import Color
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.semantic import Controlled, Details, Toggle, Uncontrolled


def indexed_children[T](children: Sequence[T], path: str) -> Iterator[tuple[T, str]]:
    """Pair each child with its position-derived path under the parent's."""
    for index, child in enumerate(children):
        yield child, f"{path}.{index}"


def branch_paths(path: str, branches: int) -> tuple[str, ...]:
    """Give each semantic fallback branch a stable path."""
    return (f"{path}.primary", *(f"{path}.alternate.{index}" for index in range(branches - 1)))


def fallback_rung(path: str, branches: int, selected: Mapping[str, int]) -> int:
    """Return one validated selected fallback rung."""
    rung = selected.get(path, 0)
    if not 0 <= rung < branches:
        message = f"{path}: planner selected unavailable fallback branch {rung}"
        raise ValueError(message)
    return rung


class PaletteHolder(Protocol):
    """Traversal state whose active palette a Themed region rescopes."""

    palette: Palette


@contextmanager
def scoped_palette(holder: PaletteHolder, palette: Palette) -> Iterator[None]:
    """Make ``palette`` the holder's active palette for the scope, then restore the previous one."""
    previous = holder.palette
    holder.palette = palette
    try:
        yield
    finally:
        holder.palette = previous


def resolve_accent(accent: Accent, palette: Palette) -> Color | None:
    """Resolve a structural accent to an exact colour: the palette brand when inherited."""
    return palette.brand if accent is AccentDefault.INHERIT else accent


def disclosure_state(node: Details[Any], session: PresentationState) -> bool:
    """Whether a disclosure is open: the author's word when controlled, session memory otherwise."""
    match node.open:
        case Controlled(value=value):
            return value
        case Uncontrolled(initial=initial):
            return session.disclosure(node.key, initial=initial).open


def toggle_state(node: Toggle, session: PresentationState) -> bool:
    """Whether a toggle is on: the author's word when controlled, session memory otherwise."""
    match node.on:
        case Controlled(value=value):
            return value
        case Uncontrolled(initial=initial):
            return session.toggle(node.key, initial=initial).on


def toggle_action_key(key: str) -> str:
    """Spell the action key for a disclosure's toggle control.

    Both planners bind the control under this key, and rebinding after a replan only finds
    the handler again because every target agrees on the spelling.
    """
    return f"{key}.toggle"


__all__ = [
    "PaletteHolder",
    "branch_paths",
    "disclosure_state",
    "fallback_rung",
    "indexed_children",
    "resolve_accent",
    "scoped_palette",
    "toggle_action_key",
    "toggle_state",
]
