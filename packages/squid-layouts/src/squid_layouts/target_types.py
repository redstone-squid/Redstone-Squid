"""Dependency-leaf static vocabulary for render targets and adapter families."""

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
    """A value whose accepted protocol target is tracked by the type checker."""

    def _accepts_target(self, target: ModeT, /) -> None:
        del target


@dataclass(frozen=True, slots=True)
class TargetRequirements[ModeT = Any]:
    """Runtime requirements carrying the same target mode as their renderable."""

    capabilities: frozenset[str] = frozenset()
