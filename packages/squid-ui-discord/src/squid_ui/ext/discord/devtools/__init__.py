"""Inspect live Discord UI sessions and operate their runtimes.

Commands authorize dashboard access; runtime policies authorize operations. Display
formatting lives in presentation so snapshots and operations remain independent of UI.
"""

from squid_ui.ext.discord.devtools.commands import DevTools, DevToolsCheck, open_devtools
from squid_ui.ext.discord.devtools.views import DevToolsSection

__all__ = ["DevTools", "DevToolsCheck", "DevToolsSection", "open_devtools"]
