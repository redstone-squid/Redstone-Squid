"""Validated access to primitive values after semantic text lowering."""

from squid_ui.emoji import Emoji, EmojiLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.text import TextLike


def text(value: TextLike) -> str:
    """Return lowered text, rejecting a primitive that escaped semantic resolution."""
    if not isinstance(value, str):
        message = f"semantic lowering left {type(value).__name__} text unresolved"
        raise LayoutInvariantError(message)
    return value


def optional_text(value: TextLike | None) -> str | None:
    """Return optional lowered text, rejecting an unresolved value."""
    return None if value is None else text(value)


def emoji(value: EmojiLike | None) -> Emoji | None:
    """Return normalized emoji metadata, rejecting shorthand past the lowering seam."""
    if value is not None and not isinstance(value, Emoji):
        message = "semantic lowering left emoji shorthand unnormalized"
        raise LayoutInvariantError(message)
    return value
