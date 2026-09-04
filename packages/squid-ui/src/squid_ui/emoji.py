"""Portable emoji metadata shared by forms, primitives, scenes, and renderers."""

from dataclasses import dataclass

__all__ = ["Emoji", "EmojiLike", "normalize_emoji"]


@dataclass(frozen=True, slots=True)
class Emoji:
    """A Unicode emoji (`id` is `None`, `name` is the character) or a custom one (`id` set, `name` its label).

    Raises `ValueError` for an empty `name`, a non-positive `id`, or `animated` without an `id`.
    """

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
    """Wrap a bare string as a Unicode `Emoji`; an `Emoji` or `None` passes through."""
    return Emoji(value) if isinstance(value, str) else value
