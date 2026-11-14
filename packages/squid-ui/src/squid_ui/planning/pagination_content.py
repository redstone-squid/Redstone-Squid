"""Materialized text pages retained before target realization and joint allocation."""

from collections.abc import Callable
from dataclasses import dataclass

from squid_ui.primitives.constraints import FIRST_PAGE, PageOwnership
from squid_ui.semantic import ListItem, Region
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class TextPages[RenderTargetT = RenderTarget](Renderable[RenderTargetT]):
    items: tuple[ListItem, ...]
    key: str
    page: PageOwnership = FIRST_PAGE
    separator: str = "\n"
    prefix: str = ""
    suffix: str = ""
    per: int | None = None
    min_fill: int = 0
    widows: int = 1
    footer: Callable[[int, int], TextLike] | None = None
    literal: bool = False


@dataclass(frozen=True, slots=True)
class RootRegion[RenderTargetT = RenderTarget](Region[RenderTargetT]):
    """A keyed document's layout pages, using its existing public pager key."""
