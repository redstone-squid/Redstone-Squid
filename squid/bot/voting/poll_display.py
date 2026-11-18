"""Poll content shared by the wizard preview and published polls."""

from collections.abc import Iterable

import squid_ui as sl


def poll_question(question: str) -> sl.semantic.Heading:
    return sl.heading(sl.plain(question))


def poll_choices(options: Iterable[tuple[str, str, str]]) -> sl.semantic.List:
    """List choices in order from their keys, labels, and optional emojis."""
    return sl.bullets(
        *(
            sl.bullet(sl.md(t"{sl.emoji_text(emoji)} {label}") if emoji else sl.plain(label), key=key)
            for key, label, emoji in options
        ),
        key="poll.options",
    )
