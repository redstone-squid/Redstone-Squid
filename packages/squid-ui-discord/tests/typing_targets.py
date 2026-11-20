"""Check which content, caches, and adapter profiles a target accepts.

The target chooses a scene dialect and an adapter implementation. These examples
check that their type parameters stay connected through planning. Concrete renderer
compatibility is checked separately in typing_renderers.py.
"""

# pyright: reportUnnecessaryTypeIgnoreComment=true
from typing import Any, assert_type

import squid_ui.ext.html as html
from squid_ui import RenderTarget, fallback
from squid_ui.authoring.semantic import FallbackContent, Paragraph
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.limits import ClassicLimits, V2Limits
from squid_ui.ext.discord.planning import classic_target, components_v2_target
from squid_ui.ext.discord.primitives import Card, Panel, Text, Variants
from squid_ui.ext.discord.target import classic, v2
from squid_ui.ext.discord.target_types import (
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
)
from squid_ui.ext.html import scene as html_scene
from squid_ui.planning import AdapterProfile, PlanCache, PlanningSnapshot, Renderable, Target, plan
from squid_ui.planning.result import PlanResult


class Portable(Renderable[RenderTarget]):
    pass


class V2Only(Renderable[ComponentsV2Target]):
    pass


def accepts_classic(value: Renderable[ClassicTarget]) -> None:
    del value


accepts_classic(Portable())
accepts_classic(V2Only())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]

discord_py = AdapterProfile(DiscordPyAdapter, "discord.py-custom", ">=2.8,<3")
discord_py_27 = AdapterProfile(DiscordPy27Adapter, "discord.py", ">=2.7,<2.8")
dynamic = AdapterProfile(DiscordAdapter, "alternate", ">=1")

assert_type(discord_py, AdapterProfile[DiscordPyAdapter])
assert_type(discord_py_27, AdapterProfile[DiscordPy27Adapter])
assert_type(dynamic, AdapterProfile[DiscordAdapter])
assert_type(
    components_v2_target(discord_py),
    Target[V2Limits, discord_scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter],
)
assert_type(
    classic_target(discord_py_27),
    Target[ClassicLimits, discord_scene.ClassicMessage, ClassicTarget, DiscordPy27Adapter],
)
assert_type(
    v2(),
    Target[V2Limits, discord_scene.ComponentsV2, ComponentsV2Target, DiscordPy27Adapter],
)
assert_type(
    classic(adapter=discord_py),
    Target[ClassicLimits, discord_scene.ClassicMessage, ClassicTarget, DiscordPyAdapter],
)


def accepts_replaceable_target(
    target: Target[V2Limits, discord_scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter],
) -> None:
    del target


accepts_replaceable_target(v2(adapter=discord_py))
accepts_replaceable_target(v2())  # pyright: ignore[reportArgumentType]  # The default adapter has a narrower family.

assert_type(plan(Text("v2"), target=v2()), PlanResult[discord_scene.ComponentsV2])
assert_type(plan(Text("classic"), target=classic()), PlanResult[discord_scene.ClassicMessage])
assert_type(plan(Paragraph("portable"), target=v2()), PlanResult[discord_scene.ComponentsV2])
assert_type(plan(Paragraph("html"), target=html.target()), PlanResult[html_scene.HtmlBody])
plan(Text("primitive"), target=html.target())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]

shared_cache = PlanCache()
html_memo = PlanningSnapshot[html_scene.HtmlBody]()
plan(Paragraph("html"), target=html.target(), cache=shared_cache, snapshot=html_memo)
plan(Paragraph("discord"), target=v2(), cache=shared_cache)
plan(Paragraph("discord"), target=v2(), snapshot=html_memo)  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]

v2_only = Panel((Text("v2"),))
classic_only = Card(children=(Text("classic"),))
plan(v2_only, target=classic())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(classic_only, target=v2())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]

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
