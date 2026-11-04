"""Plan layouts as HTML and draw HTML scenes, or preview existing Discord scenes.

Renderer draws the HTML target's native scene. DiscordPreviewRenderer instead draws
an already planned Discord Components V2 scene for inspection. Neither supplies browser
event dispatch; an interactive frontend must connect the emitted action metadata.
"""

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
