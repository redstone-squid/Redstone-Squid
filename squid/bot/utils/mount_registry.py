"""Compatibility imports for the session registry's former host-side location.

Use `squid_layouts.discord` directly. This shim remains for one release so downstream bot
extensions can migrate without an immediate import break.
"""

from squid_layouts.discord import MountRegistry, SessionKey, WhenOpen

__all__ = ["MountRegistry", "SessionKey", "WhenOpen"]
