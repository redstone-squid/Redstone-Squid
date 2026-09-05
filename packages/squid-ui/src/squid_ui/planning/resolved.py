"""Validated access to primitive values after semantic text lowering."""

from squid_ui.emoji import Emoji, EmojiLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.text import TextLike


def text(value: TextLike) -> str:
    """`value` as the `str` lowering left it; raises `LayoutInvariantError` for any other `TextLike`."""
    if not isinstance(value, str):
        message = f"semantic lowering left {type(value).__name__} text unresolved"
        raise LayoutInvariantError(message)
    return value


def optional_text(value: TextLike | None) -> str | None:
    """`text(value)`, or `None` for `None`; raises `LayoutInvariantError` for unresolved text."""
    return None if value is None else text(value)


def emoji(value: EmojiLike | None) -> Emoji | None:
    """`value` as the `Emoji` lowering left it; raises `LayoutInvariantError` for string shorthand."""
    if value is not None and not isinstance(value, Emoji):
        message = "semantic lowering left emoji shorthand unnormalized"
        raise LayoutInvariantError(message)
    return value
