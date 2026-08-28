"""Explicit machine windows resolved through the shared cursor coordinator."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from squid_ui.chrome import Chrome
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.sources import Position


@dataclass(frozen=True, slots=True)
class PagePosition:
    """A zero-based page index for a fully materialized collection."""

    index: int = 0

    def __post_init__(self) -> None:
        if self.index < 0:
            message = "PagePosition.index must not be negative"
            raise ValueError(message)


FIRST_PAGE = PagePosition()


def window[T](
    values: Sequence[T],
    *,
    key: str,
    position: PagePosition,
    per_page: int,
    chrome: Chrome,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], PagePosition, int]:
    """Slice at an explicit position using the materialized cursor policy."""
    pages = max(1, (len(values) + per_page - 1) // per_page)
    request = MaterializedCursorRequest(key, pages, content_fingerprint([identity(value) for value in values]))
    coordinator = CursorCoordinator(PresentationState(), chrome, overrides={key: Position(offset=position.index)})
    grant = coordinator.grant(request)
    index = grant.position.offset
    visible = tuple(values[index * per_page : (index + 1) * per_page])
    return visible, PagePosition(index), grant.extent
