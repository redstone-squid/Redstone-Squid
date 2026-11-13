"""Positions in already materialized layout pages, independent of source addresses."""

from dataclasses import dataclass, replace
from typing import Protocol

from squid_ui.ownership import Uncontrolled


@dataclass(frozen=True, slots=True)
class PagePosition:
    """A zero-based layout page and an optional stable content anchor."""

    anchor: str | None = None
    page: int = 0


FIRST_POSITION = PagePosition()


def resolve_page_position(
    *,
    override: PagePosition | None = None,
    anchored: PagePosition | None = None,
    stale: bool = False,
    stored: PagePosition | None = None,
    initial: PagePosition = FIRST_POSITION,
    fallback: PagePosition = FIRST_POSITION,
    upper_bound: int | None = None,
) -> PagePosition:
    """Choose a position, then clamp its page to the available bounds.

    An override wins over an anchor. Otherwise stale content uses the fallback;
    unchanged content uses the stored position or the initial position.
    """
    if override is not None:
        selected = override
    elif anchored is not None:
        selected = anchored
    elif stale:
        selected = fallback
    elif stored is not None:
        selected = stored
    else:
        selected = initial

    page = max(0, selected.page)
    if upper_bound is not None:
        page = min(page, max(0, upper_bound))
    return replace(selected, page=page)


class PagePositionPolicy(Protocol):
    """A callable selecting a position for navigation or content changes."""

    def __call__(
        self,
        *,
        override: PagePosition | None = None,
        anchored: PagePosition | None = None,
        stale: bool = False,
        stored: PagePosition | None = None,
        initial: PagePosition = FIRST_POSITION,
        fallback: PagePosition = FIRST_POSITION,
        upper_bound: int | None = None,
    ) -> PagePosition: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RevealPage(Uncontrolled[int]):
    """Reveal a keyed entry once, then let the reader turn layout pages normally.

    Change `request` for each new reveal intent. Planning stages its consumption
    with the cursor; a failed delivery leaves the intent eligible for retry.
    """

    initial: int = 0
    anchor: str
    request: str
