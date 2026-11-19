"""Plan Discord messages and draw their scenes into native payloads.

The public functions render Components V2. Classic composition uses
`squid_ui.ext.discord.classic`; both modes share result, cache, and wiring contracts.
"""

from squid_ui.ext.discord.rendering.api import render_item, render_message, render_static
from squid_ui.ext.discord.rendering.pipeline import RenderedMessage

__all__ = ["RenderedMessage", "render_item", "render_message", "render_static"]
