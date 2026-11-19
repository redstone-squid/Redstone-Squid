"""Static target markers and adapter capability declarations for HTML."""

from enum import StrEnum

from squid_ui.target_types import RenderTarget


class HtmlAdapterCapability(StrEnum):
    """Behavior verified by this target's renderer adapters."""

    RENDER_HTML = "adapter.html.render"


class HtmlTarget(RenderTarget):
    """Marker for native semantic HTML renderables."""


class HtmlAdapter:
    """Marker for adapters that mechanically draw semantic HTML scenes."""
