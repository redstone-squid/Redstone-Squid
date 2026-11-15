"""Compose portable documents as HTML and render their planned scenes."""

from squid_ui.ext.html.renderer import DEFAULT_CSS, Renderer
from squid_ui.ext.html.target import HTML_ADAPTER, HTML_LIMITS, HtmlLimits, target

__all__ = [
    "DEFAULT_CSS",
    "HTML_ADAPTER",
    "HTML_LIMITS",
    "HtmlLimits",
    "Renderer",
    "target",
]
