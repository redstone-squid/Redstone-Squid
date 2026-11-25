"""Shared paging state for widgets whose visible page comes from a batch source."""

from dataclasses import dataclass, replace
from enum import StrEnum

from squid_ui.authoring.semantic import List, Truncated, truncate
from squid_ui.chrome import Chrome
from squid_ui.content.text import TextLike
from squid_ui.planning.pagination.navigation import NavigationState, NavigationUnit
from squid_ui.sources import Chronology, CountPrecision, LoadedBatch, SourceCapabilities


@dataclass(frozen=True, slots=True)
class LoadingCopy:
    """Localized loading, failure, and retry text shared by source-backed widgets."""

    loading: TextLike = "Loading…"
    failed: TextLike = "Could not load entries."
    retry: TextLike = "Retry"
    removed: TextLike = "That item is no longer in these results."
    end: TextLike = "No more entries in this direction."


DEFAULT_LOADING_COPY = LoadingCopy()


class PageOverflow(StrEnum):
    """What a page of source items does when it cannot fit.

    A page always shows its whole batch when it can, continuing in further messages
    when the root allows more than one. This choice applies only after that fails.
    """

    SPLIT = "split"
    """Show the page in parts, each footed as part of a page too big to fit."""
    SHORTEN = "shorten"
    """Shorten the longest items until the page fits."""

    def listing(self, items: List, *, fetch_size: int, chrome: Chrome) -> List | Truncated:
        """Keep one batch's items together as one page, with this fallback."""
        if self is PageOverflow.SHORTEN:
            return truncate(replace(items, group_size=fetch_size))
        return replace(items, group_size=fetch_size, split_footer=chrome.split_page_footer)


def source_navigation_state[ItemT](
    loaded: LoadedBatch[ItemT],
    capabilities: SourceCapabilities,
    *,
    key: str,
    fetch_size: int,
    chrome: Chrome,
    chronology: Chronology | None = None,
) -> NavigationState | None:
    """Build navigation facts for an addressable source batch, if it can move.

    An exact jumpable source stays navigable on its last page so the jump control can
    still take the reader elsewhere. The buttons say Previous and Next unless `chronology`
    says how the items are sorted in time.
    """
    batch = loaded.batch
    extent = capabilities.group_count(batch, fetch_size) if capabilities.jumpable else None
    if not (batch.has_next or (capabilities.backward and batch.has_previous) or (extent or 0) > 1):
        return None
    total = batch.item_count if capabilities.count is not CountPrecision.NONE else None
    previous_label, next_label = step_labels(chrome, chronology)
    visible_range = (
        (batch.position.offset + 1, batch.position.offset + len(batch.items))
        if capabilities.offsets and batch.items
        else None
    )
    return NavigationState(
        key=key,
        has_previous=batch.has_previous,
        has_next=batch.has_next,
        backward=capabilities.backward,
        previous_label=previous_label,
        next_label=next_label,
        previous_key=f"{key}.previous",
        next_key=f"{key}.next",
        extent=extent,
        index=batch.position.offset // fetch_size if capabilities.fixed_groups else None,
        unit=NavigationUnit.GROUPS if capabilities.fixed_groups else NavigationUnit.ITEMS,
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
    "PageOverflow",
    "source_navigation_state",
    "step_labels",
]
