"""Static target and adapter vocabulary for Discord."""

from squid_ui.target_types import RenderTarget


class DiscordTarget(RenderTarget):
    """Marker for values renderable to any Discord component mode."""


class ComponentsV2Target(DiscordTarget):
    """Marker for Discord Components V2 renderables."""


class ClassicTarget(DiscordTarget):
    """Marker for classic Discord message renderables."""


class DiscordAdapter:
    """Marker for any adapter capable of realizing a Discord target."""


class DiscordPyAdapter(DiscordAdapter):
    """Marker for adapters implemented with discord.py."""


class DiscordPy27Adapter(DiscordPyAdapter):
    """Marker for Squid's verified discord.py 2.7 adapter."""
