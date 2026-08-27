"""Dependency-free HTML renderer for resolved squid-ui scenes."""

from squid_ui.html.renderer import PREVIEW_CSS, Renderer
from squid_ui.html.target import HTML_ADAPTER, HTML_LIMITS, HtmlLimits, target

__all__ = ["HTML_ADAPTER", "HTML_LIMITS", "PREVIEW_CSS", "HtmlLimits", "Renderer", "target"]
