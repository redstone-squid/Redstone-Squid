"""Html protocol and renderer behavior declarations."""

from enum import StrEnum


class HtmlAdapterCapability(StrEnum):
    """Behavior verified by this target's renderer adapters."""

    RENDER_HTML = "adapter.html.render"
