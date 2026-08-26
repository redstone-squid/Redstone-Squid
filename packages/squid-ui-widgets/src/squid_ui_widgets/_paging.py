"""Explicit machine windows resolved through the shared cursor coordinator."""

from collections.abc import Callable, Sequence

from squid_ui.chrome import Chrome
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_ui.runtime.presentation import PresentationSession
from squid_ui.sources import Position


def window[T](
    values: Sequence[T],
    *,
    key: str,
    position: Position,
    per_page: int,
    chrome: Chrome,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], Position, int]:
    """Slice at an explicit position using the materialized cursor policy."""
    pages = max(1, (len(values) + per_page - 1) // per_page)
    request = MaterializedCursorRequest(key, pages, content_fingerprint([identity(value) for value in values]))
    coordinator = CursorCoordinator(PresentationSession(), chrome, overrides={key: position})
    grant = coordinator.grant(request)
    index = grant.position.offset
    visible = tuple(values[index * per_page : (index + 1) * per_page])
    return visible, grant.position, grant.extent
