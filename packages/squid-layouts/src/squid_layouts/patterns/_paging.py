"""Explicit pattern windows resolved through the shared page broker."""

from collections.abc import Callable, Sequence

from squid_layouts.chrome import Chrome
from squid_layouts.planning.cursors import PageBroker, PageRequest, content_fingerprint
from squid_layouts.runtime.presentation import PresentationSession


def window[T](
    values: Sequence[T],
    *,
    key: str,
    page: int,
    per_page: int,
    chrome: Chrome,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], int, int]:
    """Slice an explicit page using the same clamping policy as planner pagination."""
    pages = max(1, (len(values) + per_page - 1) // per_page)
    request = PageRequest(key, pages, content_fingerprint([identity(value) for value in values]))
    broker = PageBroker(PresentationSession(), chrome, overrides={key: page})
    grant = broker.grant(request)
    visible = tuple(values[grant.index * per_page : (grant.index + 1) * per_page])
    return visible, grant.index, grant.pages
