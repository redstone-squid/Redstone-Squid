"""Dependency-leaf static vocabulary for render targets and adapter families."""


class RenderTarget:
    """Marker for portable values renderable by every semantic target."""


class DiscordTarget(RenderTarget):
    """Marker for values renderable to any Discord component mode."""


class ComponentsV2Target(DiscordTarget):
    """Marker for Discord Components V2 renderables."""


class ClassicTarget(DiscordTarget):
    """Marker for classic Discord message renderables."""


class HtmlTarget(RenderTarget):
    """Marker for native semantic HTML renderables."""


class DiscordAdapter:
    """Marker for any adapter capable of realizing a Discord target."""


class DiscordPyAdapter(DiscordAdapter):
    """Marker for adapters implemented with discord.py."""


class DiscordPy27Adapter(DiscordPyAdapter):
    """Marker for Squid's verified discord.py 2.7 adapter."""


class HtmlAdapter:
    """Marker for adapters that mechanically draw semantic HTML scenes."""


class Renderable[ModeT = RenderTarget]:
    """A value whose accepted protocol target is tracked by the type checker."""

    def _accepts_target(self, target: ModeT, /) -> None:
        del target
