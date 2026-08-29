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


class SlackTarget(RenderTarget):
    """Marker for values renderable to any Slack Block Kit surface."""


class SlackMessageTarget(SlackTarget):
    """Marker for Slack message renderables."""


class SlackModalTarget(SlackTarget):
    """Marker for Slack modal renderables."""


class SlackHomeTarget(SlackTarget):
    """Marker for Slack App Home renderables."""


class DiscordAdapter:
    """Marker for any adapter capable of realizing a Discord target."""


class DiscordPyAdapter(DiscordAdapter):
    """Marker for adapters implemented with discord.py."""


class DiscordPy27Adapter(DiscordPyAdapter):
    """Marker for Squid's verified discord.py 2.7 adapter."""


class HtmlAdapter:
    """Marker for adapters that mechanically draw semantic HTML scenes."""


class SlackAdapter:
    """Marker for adapters capable of realizing Slack Block Kit targets."""


class SlackSdkAdapter(SlackAdapter):
    """Marker for adapters implemented with the Slack Python SDK."""


class SlackSdk343Adapter(SlackSdkAdapter):
    """Marker for Squid's verified Slack Python SDK 3.43 adapter."""


class Renderable[RenderTargetT = RenderTarget]:
    """A value whose accepted protocol target is tracked by the type checker."""

    __slots__ = ()
    """Empty, so a `slots=True` node subclass really is slotted.

    A base without `__slots__` grants every subclass a `__dict__`, which silently defeated
    the `@dataclass(frozen=True, slots=True)` on every node in the package.
    """

    def _accepts_target(self, target: RenderTargetT, /) -> None:
        del target
