"""Pins target-mode variance and adapter profile families under the project type check.

Renderer protocol conformance moved to `typing_renderers.py`.
"""

from typing import Any, assert_type

from squid_ui import RenderTarget, fallback, html, scene
from squid_ui.planning import (
    AdapterProfile,
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    PlanCache,
    PlanMemo,
    Renderable,
    Target,
    classic_target,
    components_v2_target,
    plan,
)
from squid_ui.planning.limits import ClassicLimits, V2Limits
from squid_ui.primitives import Card, Panel, Text, Variants
from squid_ui.scene.model import PlanResult
from squid_ui.semantic import FallbackContent, Paragraph
from squid_ui_discord.target import classic, v2


class Portable(Renderable[RenderTarget]):
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
assert_type(plan(Paragraph("portable"), target=v2()), PlanResult[scene.ComponentsV2])
assert_type(plan(Paragraph("html"), target=html.target()), PlanResult[scene.HtmlBody])
plan(Text("primitive"), target=html.target())  # pyrefly: ignore[bad-argument-type]

html_cache = PlanCache[scene.HtmlBody]()
html_memo = PlanMemo[scene.HtmlBody]()
plan(Paragraph("html"), target=html.target(), cache=html_cache, memo=html_memo)
plan(Paragraph("discord"), target=v2(), cache=html_cache)  # pyrefly: ignore[bad-argument-type]
plan(Paragraph("discord"), target=v2(), memo=html_memo)  # pyrefly: ignore[bad-argument-type]

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
