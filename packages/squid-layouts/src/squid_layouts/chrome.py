"""Host-supplied framework strings.

The package may not contain `_()` markers (the host's Babel setup extracts only from its own
tree), so every user-visible string the framework produces enters through this table.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

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


def _default_page_option(page: int) -> TextLike:
    return f"Page {page}"


def _default_decided(label: TextLike) -> TextLike:
    return f"You chose {label}."


def _default_try_again_in(seconds: float) -> TextLike:
    whole = max(1, ceil(seconds))
    return f"Try again in {whole} second{'' if whole == 1 else 's'}."


@dataclass(frozen=True, slots=True)
class Chrome:
    and_n_more: Callable[[int], TextLike] = _default_and_n_more
    """Spill line appended when a list shows fewer entries than it holds."""
    not_yours: TextLike = "These controls belong to someone else."
    """Ephemeral rejection shown when an author-locked control is used by another user."""
    session_ended: TextLike = "This session has ended."
    """Ephemeral rejection shown when a control on a finished mount is clicked anyway."""
    not_now: TextLike = "You can't do that right now."
    """Ephemeral rejection shown when an action's guard denies a press with no wording."""
    try_again_in: Callable[[float], TextLike] = _default_try_again_in
    """Denial wording when the guard said how long to wait; called with seconds."""
    working: TextLike = "Working\N{HORIZONTAL ELLIPSIS}"
    """What a control with `Feedback` says while its handler runs."""
    updates_paused: TextLike = "Live updates paused — press any control to resume."
    """Status shown before an interaction edit token expires and unattended refreshes pause."""
    previous: TextLike = "Previous"
    next: TextLike = "Next"
    older: TextLike = "Older"
    newer: TextLike = "Newer"
    back: TextLike = "Back"
    home: TextLike = "Home"
    close: TextLike = "Close"
    undo: TextLike = "Undo"
    redo: TextLike = "Redo"
    on: TextLike = "On"
    off: TextLike = "Off"
    download: TextLike = "Download"
    confirm: TextLike = "Confirm"
    cancel: TextLike = "Cancel"
    apply: TextLike = "Apply"
    save: TextLike = "Save"
    unsaved: TextLike = "Unsaved changes"
    search: TextLike = "Search"
    no_results: TextLike = "No results"
    decided: Callable[[TextLike], TextLike] = _default_decided
    add: TextLike = "Add"
    edit: TextLike = "Edit"
    remove: TextLike = "Remove"
    move_up: TextLike = "Move up"
    move_down: TextLike = "Move down"
    review: TextLike = "Review"
    finish: TextLike = "Finish"
    unanswered: TextLike = "Not answered yet"
    page_footer: Callable[[int, int], TextLike] = _default_page_footer
    """Small-text footer under paginated content; called with (page, pages), 1-based."""
    range_footer: Callable[[int, int], TextLike] = _default_range_footer
    """Visible 1-based item range for a source with known offsets."""
    approximate_total_footer: Callable[[int, int, int], TextLike] = _default_approximate_total_footer
    """Visible range and approximate source total."""
    total_range_footer: Callable[[int, int, int], TextLike] = _default_total_range_footer
    """Visible range and exact source total."""
    jump_to_page: TextLike = "Jump to a page"
    """Placeholder on the jump select a seekable paginator can offer."""
    page_option: Callable[[int], TextLike] = _default_page_option
    """One entry of that select; called with a 1-based page number."""


DEFAULT_CHROME = Chrome()
CHROME_CONTEXT = ContextKey[Chrome]("chrome")
LOCALIZATION_CONTEXT = ContextKey[Localization]("localization")


def localize_chrome(chrome: Chrome, localization: Localization) -> Chrome:
    """Resolve host chrome once before planning and navigation consume it."""
    return Chrome(
        and_n_more=lambda count: resolve_text(chrome.and_n_more(count), localization).content,
        not_yours=resolve_text(chrome.not_yours, localization).content,
        session_ended=resolve_text(chrome.session_ended, localization).content,
        not_now=resolve_text(chrome.not_now, localization).content,
        try_again_in=lambda seconds: resolve_text(chrome.try_again_in(seconds), localization).content,
        working=resolve_text(chrome.working, localization).content,
        updates_paused=resolve_text(chrome.updates_paused, localization).content,
        previous=resolve_text(chrome.previous, localization).content,
        next=resolve_text(chrome.next, localization).content,
        older=resolve_text(chrome.older, localization).content,
        newer=resolve_text(chrome.newer, localization).content,
        back=resolve_text(chrome.back, localization).content,
        home=resolve_text(chrome.home, localization).content,
        close=resolve_text(chrome.close, localization).content,
        undo=resolve_text(chrome.undo, localization).content,
        redo=resolve_text(chrome.redo, localization).content,
        on=resolve_text(chrome.on, localization).content,
        off=resolve_text(chrome.off, localization).content,
        download=resolve_text(chrome.download, localization).content,
        confirm=resolve_text(chrome.confirm, localization).content,
        cancel=resolve_text(chrome.cancel, localization).content,
        apply=resolve_text(chrome.apply, localization).content,
        save=resolve_text(chrome.save, localization).content,
        unsaved=resolve_text(chrome.unsaved, localization).content,
        search=resolve_text(chrome.search, localization).content,
        no_results=resolve_text(chrome.no_results, localization).content,
        decided=lambda label: resolve_text(chrome.decided(label), localization).content,
        add=resolve_text(chrome.add, localization).content,
        edit=resolve_text(chrome.edit, localization).content,
        remove=resolve_text(chrome.remove, localization).content,
        move_up=resolve_text(chrome.move_up, localization).content,
        move_down=resolve_text(chrome.move_down, localization).content,
        review=resolve_text(chrome.review, localization).content,
        finish=resolve_text(chrome.finish, localization).content,
        unanswered=resolve_text(chrome.unanswered, localization).content,
        page_footer=lambda page, pages: resolve_text(chrome.page_footer(page, pages), localization).content,
        range_footer=lambda first, last: resolve_text(chrome.range_footer(first, last), localization).content,
        approximate_total_footer=lambda first, last, total: (
            resolve_text(chrome.approximate_total_footer(first, last, total), localization).content
        ),
        total_range_footer=lambda first, last, total: (
            resolve_text(chrome.total_range_footer(first, last, total), localization).content
        ),
        jump_to_page=resolve_text(chrome.jump_to_page, localization).content,
        page_option=lambda page: resolve_text(chrome.page_option(page), localization).content,
    )
