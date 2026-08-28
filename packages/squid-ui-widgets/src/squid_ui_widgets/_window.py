"""Shared paging state for widgets whose visible page comes from a window source."""

from dataclasses import dataclass
from typing import Literal

from squid_ui.errors import LayoutInvariantError
from squid_ui.runtime.resources import Failed, Pending, ResourceStatus
from squid_ui.sources import LoadedWindow, Position, WindowLoader
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


__all__ = ["DEFAULT_LOADING_COPY", "LoadingCopy", "WindowRequest", "last_ready", "load_window"]
