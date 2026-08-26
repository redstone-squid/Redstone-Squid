"""Pins target-mode variance and adapter profile families under the project type check."""

from typing import Any, assert_type

from squid_discord.classic_renderer import ClassicRenderer
from squid_discord.presentation import DiscordPresentation
from squid_discord.renderer import V2Renderer
from squid_discord.target import classic, v2
from squid_ui import fallback, scene
from squid_ui.html import Renderer as HtmlRenderer
from squid_ui.planning import (
    AdapterProfile,
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
    Renderable,
    Target,
    classic_target,
    components_v2_target,
    plan,
)
from squid_ui.planning.limits import ClassicLimits, V2Limits
from squid_ui.primitives import Card, Panel, Text, Variants
from squid_ui.renderer import Renderer
from squid_ui.scene.model import PlanResult
from squid_ui.semantic import FallbackContent


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
    Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter],
)
assert_type(
    classic_target(discord_py_27),
    Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPy27Adapter],
)
assert_type(
    v2(),
    Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPy27Adapter],
)
assert_type(
    classic(adapter=discord_py),
    Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPyAdapter],
)
assert_type(plan(Text("v2"), target=v2()), PlanResult[scene.ComponentsV2])
assert_type(plan(Text("classic"), target=classic()), PlanResult[scene.ClassicMessage])

v2_only = Panel((Text("v2"),))
classic_only = Card(children=(Text("classic"),))
plan(v2_only, target=classic())  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(classic_only, target=v2())  # pyrefly: ignore[no-matching-overload, bad-argument-type]

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


def accepts_v2_renderer(value: Renderer[scene.ComponentsV2, DiscordPresentation]) -> None:
    del value


def accepts_classic_renderer(value: Renderer[scene.ClassicMessage, DiscordPresentation]) -> None:
    del value


def accepts_html_renderer(value: Renderer[scene.ComponentsV2, str]) -> None:
    del value


# A declared protocol nothing implements is how the contravariance bug survived: `draw` took
# an unparameterized `scene.Document`, so no renderer that narrowed to its own body could
# satisfy it. These three pin that it is satisfiable.
accepts_v2_renderer(V2Renderer())
accepts_classic_renderer(ClassicRenderer())
accepts_html_renderer(HtmlRenderer())
