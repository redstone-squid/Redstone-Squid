"""Shared paging state for widgets whose visible page comes from a window source."""

from dataclasses import dataclass

from squid_ui.chrome import Chrome
from squid_ui.planning.navigation import NavigationState
from squid_ui.sources import Chronology, CountPrecision, LoadedWindow, SourceCapabilities
from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class LoadingCopy:
    """Localized loading, failure, and retry text shared by source-backed widgets."""

    loading: TextLike = "Loading…"
    failed: TextLike = "Could not load entries."
    retry: TextLike = "Retry"
    removed: TextLike = "That item is no longer in these results."
    end: TextLike = "No more entries in this direction."


DEFAULT_LOADING_COPY = LoadingCopy()


def source_navigation_state[ItemT](
    loaded: LoadedWindow[ItemT],
    capabilities: SourceCapabilities,
    *,
    key: str,
    page_size: int,
    chrome: Chrome,
    chronology: Chronology | None = None,
) -> NavigationState | None:
    """Build navigation facts for an addressable source window, if it can move.

    An exact jumpable source stays navigable on its last page so the jump control can
    still take the reader elsewhere. The buttons say Previous and Next unless `chronology`
    says how the items are sorted in time.
    """
    window = loaded.window
    extent = (
        max(1, (window.total + page_size - 1) // page_size)
        if capabilities.count is CountPrecision.EXACT and capabilities.jumpable and window.total is not None
        else None
    )
    if not (window.has_next or (capabilities.backward and window.has_previous) or (extent or 0) > 1):
        return None
    total = window.total if capabilities.count is not CountPrecision.NONE else None
    previous_label, next_label = step_labels(chrome, chronology)
    visible_range = (
        (window.position.offset + 1, window.position.offset + len(window.items))
        if capabilities.offsets and window.items
        else None
    )
    return NavigationState(
        key=key,
        has_previous=window.has_previous,
        has_next=window.has_next,
        backward=capabilities.backward,
        previous_label=previous_label,
        next_label=next_label,
        previous_key=f"{key}.previous",
        next_key=f"{key}.next",
        extent=extent,
        page=window.position.offset // page_size if capabilities.offsets else None,
        visible_range=visible_range,
        total=total,
        count=capabilities.count,
        seek_key=f"{key}.seek",
        seek_label=chrome.jump_to_page,
        page_option=chrome.page_option,
    )


def step_labels(chrome: Chrome, chronology: Chronology | None) -> tuple[TextLike, TextLike]:
    """Labels for stepping toward the start and the end of a list."""
    return (chrome.previous, chrome.next) if chronology is None else chronology.step_labels(chrome)


__all__ = [
    "DEFAULT_LOADING_COPY",
    "LoadingCopy",
    "source_navigation_state",
    "step_labels",
]
