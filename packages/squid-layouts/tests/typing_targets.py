"""Pins target-mode variance and adapter profile families under the project type check."""

from typing import Any, assert_type

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
from squid_layouts.scene.model import PlanResult, SceneClassicMessage, SceneComponentsV2
from squid_layouts.planning import TargetProfile
from squid_layouts import fallback, plan
from squid_layouts.primitives import Card, Panel, Text, Variants
from squid_layouts.semantic import FallbackContent


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
assert_type(plan(Text("v2"), target=Target.v2()), PlanResult[SceneComponentsV2])
assert_type(plan(Text("classic"), target=Target.classic()), PlanResult[SceneClassicMessage])

v2_only = Panel((Text("v2"),))
classic_only = Card(children=(Text("classic"),))
plan(v2_only, target=Target.classic())  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(classic_only, target=Target.v2())  # pyrefly: ignore[no-matching-overload, bad-argument-type]

assert_type(
    fallback(v2_only, classic_only),
    FallbackContent[ComponentsV2Target | ClassicTarget],
)
assert_type(
    Variants.of(v2_only, classic_only),
    Variants[ComponentsV2Target | ClassicTarget],
)
assert_type(
    Variants.of(v2_only, classic_only, Text("a"), Text("b"), Text("c"), Text("long")),
    Variants[Any],
)
