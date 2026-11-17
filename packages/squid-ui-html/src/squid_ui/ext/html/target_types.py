"""Static target and adapter vocabulary for Html."""

from squid_ui.target_types import RenderTarget


class HtmlTarget(RenderTarget):
    """Marker for native semantic HTML renderables."""


class HtmlAdapter:
    """Marker for adapters that mechanically draw semantic HTML scenes."""
