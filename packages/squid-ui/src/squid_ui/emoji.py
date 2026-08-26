"""Portable emoji metadata shared by forms, primitives, scenes, and renderers."""

from dataclasses import dataclass

__all__ = ["Emoji", "EmojiLike", "normalize_emoji"]


@dataclass(frozen=True, slots=True)
class Emoji:
    """A Unicode or Discord custom emoji."""

    name: str
    id: int | None = None
    animated: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            message = "Emoji name must not be empty"
            raise ValueError(message)
        if self.id is not None and self.id <= 0:
            message = "Emoji id must be positive"
            raise ValueError(message)
        if self.id is None and self.animated:
            message = "Unicode emoji cannot be animated"
            raise ValueError(message)


type EmojiLike = str | Emoji


def normalize_emoji(value: EmojiLike | None) -> Emoji | None:
    """Normalize shorthand Unicode emoji to the public metadata value."""
    return Emoji(value) if isinstance(value, str) else value
