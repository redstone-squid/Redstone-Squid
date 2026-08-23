"""Static target-mode and adapter-family vocabulary for render planning."""

from dataclasses import dataclass
from typing import Any


class DiscordTarget:
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


class Renderable[ModeT = DiscordTarget]:
    """A value whose accepted protocol target is tracked by the type checker.

    The private method exists only to make ``ModeT`` contravariant. A portable
    ``Renderable[DiscordTarget]`` can therefore be used for either Discord mode,
    while a V2-only renderable cannot be passed to a classic target.
    """

    def _accepts_target(self, target: ModeT, /) -> None:
        del target


@dataclass(frozen=True, slots=True)
class TargetRequirements[ModeT = Any]:
    """Runtime requirements carrying the same target mode as their renderable."""

    capabilities: frozenset[str] = frozenset()

