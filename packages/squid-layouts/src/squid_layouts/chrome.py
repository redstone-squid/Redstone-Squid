"""Host-supplied framework strings.

The package may not contain `_()` markers (the host's Babel setup extracts only from its own
tree), so every user-visible string the framework produces enters through this table.
"""

from collections.abc import Callable
from dataclasses import dataclass

from squid_layouts.runtime.context import ContextKey
from squid_layouts.text import Localization, TextLike, resolve_text


def _default_and_n_more(count: int) -> TextLike:
    return f"…and {count} more"


def _default_page_footer(page: int, pages: int) -> TextLike:
    return f"Page {page} of {pages}"


def _default_range_footer(first: int, last: int) -> TextLike:
    return f"{first}\N{EN DASH}{last}"


def _default_approximate_total_footer(first: int, last: int, total: int) -> TextLike:
    return f"{first}\N{EN DASH}{last} of ~{total}"


def _default_total_range_footer(first: int, last: int, total: int) -> TextLike:
    return f"{first}\N{EN DASH}{last} of {total}"


@dataclass(frozen=True, slots=True)
class Chrome:
    and_n_more: Callable[[int], TextLike] = _default_and_n_more
    """Spill line appended when a list shows fewer entries than it holds."""
    not_yours: TextLike = "These controls belong to someone else."
    """Ephemeral rejection shown when an author-locked control is used by another user."""
    session_ended: TextLike = "This session has ended."
    """Ephemeral rejection shown when a control on a finished mount is clicked anyway."""
    previous: TextLike = "Previous"
    next: TextLike = "Next"
    older: TextLike = "Older"
    newer: TextLike = "Newer"
    back: TextLike = "Back"
    home: TextLike = "Home"
    close: TextLike = "Close"
    page_footer: Callable[[int, int], TextLike] = _default_page_footer
    """Small-text footer under paginated content; called with (page, pages), 1-based."""
    range_footer: Callable[[int, int], TextLike] = _default_range_footer
    """Visible 1-based item range for a source with known offsets."""
    approximate_total_footer: Callable[[int, int, int], TextLike] = _default_approximate_total_footer
    """Visible range and approximate source total."""
    total_range_footer: Callable[[int, int, int], TextLike] = _default_total_range_footer
    """Visible range and exact source total."""


DEFAULT_CHROME = Chrome()
CHROME_CONTEXT = ContextKey[Chrome]("chrome")
LOCALIZATION_CONTEXT = ContextKey[Localization]("localization")


def localize_chrome(chrome: Chrome, localization: Localization) -> Chrome:
    """Resolve host chrome once before planning and navigation consume it."""
    return Chrome(
        and_n_more=lambda count: resolve_text(chrome.and_n_more(count), localization).content,
        not_yours=resolve_text(chrome.not_yours, localization).content,
        session_ended=resolve_text(chrome.session_ended, localization).content,
        previous=resolve_text(chrome.previous, localization).content,
        next=resolve_text(chrome.next, localization).content,
        older=resolve_text(chrome.older, localization).content,
        newer=resolve_text(chrome.newer, localization).content,
        back=resolve_text(chrome.back, localization).content,
        home=resolve_text(chrome.home, localization).content,
        close=resolve_text(chrome.close, localization).content,
        page_footer=lambda page, pages: resolve_text(chrome.page_footer(page, pages), localization).content,
        range_footer=lambda first, last: resolve_text(chrome.range_footer(first, last), localization).content,
        approximate_total_footer=lambda first, last, total: (
            resolve_text(chrome.approximate_total_footer(first, last, total), localization).content
        ),
        total_range_footer=lambda first, last, total: (
            resolve_text(chrome.total_range_footer(first, last, total), localization).content
        ),
    )
