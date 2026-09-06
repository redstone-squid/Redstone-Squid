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
    """Target of a `seek`; the other operations ignore it."""


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

    A `previous` or `next` with no `previous` page reloads at the anchor rather than guessing
    a position. `subject` names the widget in the error message.

    Raises:
        LayoutInvariantError: The loader returned no window: a newer request superseded this
            one (the resource runs again for it), or a `previous`/`next` step had no page on
            that side.
        ValueError: The fetched window contradicts the source's `capabilities`; see
            `squid_ui.sources.WindowLoader.load`.
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
