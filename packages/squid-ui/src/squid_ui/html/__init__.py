"""First-class semantic HTML planning and safe scene drawing."""

from squid_ui.html.discord_preview import PREVIEW_CSS, DiscordPreviewRenderer
from squid_ui.html.renderer import DEFAULT_CSS, Renderer
from squid_ui.html.target import HTML_ADAPTER, HTML_LIMITS, HtmlLimits, target

__all__ = [
    "DEFAULT_CSS",
    "HTML_ADAPTER",
    "HTML_LIMITS",
    "PREVIEW_CSS",
    "DiscordPreviewRenderer",
    "HtmlLimits",
    "Renderer",
    "target",
]
