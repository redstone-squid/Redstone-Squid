"""Shared paging state for widgets whose visible page comes from a window source."""

from dataclasses import dataclass
from typing import Literal

from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.navigation import NavigationState
from squid_ui.runtime.resources import Failed, Pending, ResourceStatus
from squid_ui.sources import CountPrecision, LoadedWindow, Position, SourceCapabilities, WindowLoader
from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class WindowRequest:
    """Which way the reader last asked to move, held as component state.

    A request rather than a direct call because the move has to survive until the resource
    next loads: the control that pressed it returns immediately, and the loader runs under
    the resource.
    """

    operation: Literal["refresh", "previous", "next", "seek"] = "refresh"
    position: Position | None = None


@dataclass(frozen=True, slots=True)
class LoadingCopy:
    """What a source-backed widget says while its window is in flight or has failed.

    One value rather than three keywords because a host localizes all three together, and
    a host with several such widgets wants to say it once.
    """

    loading: TextLike = "Loading…"
    failed: TextLike = "Could not load entries."
    retry: TextLike = "Retry"


DEFAULT_LOADING_COPY = LoadingCopy()


def last_ready[ValueT](status: ResourceStatus[ValueT]) -> ValueT | None:
    """The value still worth showing while a reload is in flight or after it failed."""
    if isinstance(status, Pending | Failed) and status.previous is not None:
        return status.previous.value
    return None


async def load_window[ItemT](
    loader: WindowLoader[ItemT],
    request: WindowRequest,
    *,
    previous: LoadedWindow[ItemT] | None,
    subject: str,
) -> LoadedWindow[ItemT]:
    """Move one window the way `request` asks, anchored on the page currently shown.

    Stepping needs a page to step from, so a `previous` or `next` without one falls back to
    loading at the anchor rather than guessing a position.

    Raises:
        LayoutInvariantError: A newer request replaced this one before it finished. The
            resource will run again for the newer one, so there is no window to return.
    """
    match request:
        case WindowRequest("previous") if previous is not None:
            loaded = await loader.previous(previous)
        case WindowRequest("next") if previous is not None:
            loaded = await loader.next(previous)
        case WindowRequest("seek", position):
            loaded = await loader.load(position, previous=previous)
        case _:
            loaded = await loader.load(previous=previous)
    if loaded is None:
        message = f"{subject} window request was superseded before it loaded"
        raise LayoutInvariantError(message)
    return loaded


def source_navigation_state[ItemT](
    loaded: LoadedWindow[ItemT],
    capabilities: SourceCapabilities,
    *,
    key: str,
    page_size: int,
    chrome: Chrome,
) -> NavigationState | None:
    """Build navigation facts for an addressable source window, if it can move.

    An exact jumpable source stays navigable on its last page so the jump control can
    still take the reader elsewhere.
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
    visible_range = (
        (window.position.offset + 1, window.position.offset + len(window.items))
        if capabilities.offsets and window.items
        else None
    )
    return NavigationState(
        key=key,
        position=window.position,
        has_previous=window.has_previous,
        has_next=window.has_next,
        backward=capabilities.backward,
        previous_label=chrome.older,
        next_label=chrome.newer,
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


__all__ = [
    "DEFAULT_LOADING_COPY",
    "LoadingCopy",
    "WindowRequest",
    "last_ready",
    "load_window",
    "source_navigation_state",
]
