"""Pins target-mode variance and adapter profile families under the project type check."""

from typing import assert_type

from squid_layouts.planning import (
    AdapterProfile,
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
    Renderable,
    classic_target,
    components_v2_target,
)
from squid_layouts.discord import Target
from squid_layouts.scene.model import SceneClassicMessage, SceneComponentsV2
from squid_layouts.planning import TargetProfile


class Portable(Renderable[DiscordTarget]):
    pass


class V2Only(Renderable[ComponentsV2Target]):
    pass


def accepts_classic(value: Renderable[ClassicTarget]) -> None:
    del value


accepts_classic(Portable())
accepts_classic(V2Only())  # pyrefly: ignore[bad-argument-type]

discord_py = AdapterProfile(DiscordPyAdapter, "discord.py-custom", ">=2.8,<3")
discord_py_27 = AdapterProfile(DiscordPy27Adapter, "discord.py", ">=2.7,<2.8")
dynamic = AdapterProfile(DiscordAdapter, "alternate", ">=1")

assert_type(discord_py, AdapterProfile[DiscordPyAdapter])
assert_type(discord_py_27, AdapterProfile[DiscordPy27Adapter])
assert_type(dynamic, AdapterProfile[DiscordAdapter])
assert_type(
    components_v2_target(discord_py),
    TargetProfile[ComponentsV2Target, DiscordPyAdapter, SceneComponentsV2],
)
assert_type(
    classic_target(discord_py_27),
    TargetProfile[ClassicTarget, DiscordPy27Adapter, SceneClassicMessage],
)
assert_type(
    Target.v2(),
    Target[ComponentsV2Target, DiscordPy27Adapter, SceneComponentsV2],
)
assert_type(
    Target.classic(adapter=discord_py),
    Target[ClassicTarget, DiscordPyAdapter, SceneClassicMessage],
)
