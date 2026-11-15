"""A keyed document root using the shared region contract."""

from dataclasses import dataclass

from squid_ui.authoring.semantic import Region
from squid_ui.target_types import RenderTarget


@dataclass(frozen=True, slots=True)
class RootRegion[RenderTargetT = RenderTarget](Region[RenderTargetT]):
    """A keyed document's layout pages, using its existing public pager key."""
