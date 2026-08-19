"""Pre-translated framework strings.

The package may not contain `_()` markers (the host's Babel setup extracts only from its own
tree), so every user-visible string the framework produces enters through this table. Hosts
build one Chrome per locale; the defaults are untranslated English.
"""

from collections.abc import Callable
from dataclasses import dataclass


def _default_and_n_more(count: int) -> str:
    return f"…and {count} more"


def _default_page_footer(page: int, pages: int) -> str:
    return f"Page {page} of {pages}"


@dataclass(frozen=True, slots=True)
class Chrome:
    and_n_more: Callable[[int], str] = _default_and_n_more
    """Spill line appended when a list shows fewer entries than it holds."""
    see_attachment: str = "See attachment"
    """Pointer left in place of content that moved to an attached file."""
    not_yours: str = "These controls belong to someone else."
    """Ephemeral rejection shown when an author-locked control is used by another user."""
    previous: str = "Previous"
    next: str = "Next"
    page_footer: Callable[[int, int], str] = _default_page_footer
    """Small-text footer under paginated content; called with (page, pages), 1-based."""


DEFAULT_CHROME = Chrome()
