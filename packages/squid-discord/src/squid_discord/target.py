"""Discord target conveniences bound to the shipped discord.py adapter."""

from collections.abc import Callable
from typing import overload

import discord

from squid_discord.adapter import DISCORD_PY_27_ADAPTER
from squid_ui import scene
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import (
    CLASSIC_PROTOCOL_CAPABILITIES,
    V2_PROTOCOL_CAPABILITIES,
    classic_target,
    components_v2_target,
)
from squid_ui.planning.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_ui.planning.target import Target
from squid_ui.primitives.nodes import Extension, PrimitiveNode
from squid_ui.target_types import (
    ClassicTarget,
    ComponentsV2Target,
    DiscordPy27Adapter,
    DiscordPyAdapter,
)

V2_CAPABILITIES = DISCORD_PY_27_ADAPTER.combine_capabilities(V2_PROTOCOL_CAPABILITIES)
CLASSIC_CAPABILITIES = CLASSIC_PROTOCOL_CAPABILITIES


@overload
def v2(
    *, limits: V2Limits = LIMITS
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPy27Adapter]: ...
@overload
def v2[ProfileT: DiscordPyAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: V2Limits = LIMITS
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, ProfileT]: ...
def v2(
    *,
    adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
    limits: V2Limits = LIMITS,
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter]:
    """A Components V2 target realized by discord.py."""
    return components_v2_target(adapter, limits=limits)


@overload
def classic(
    *, limits: ClassicLimits = CLASSIC_LIMITS
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPy27Adapter]: ...
@overload
def classic[ProfileT: DiscordPyAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: ClassicLimits = CLASSIC_LIMITS
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, ProfileT]: ...
def classic(
    *,
    adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPyAdapter]:
    """A classic-message target realized by discord.py."""
    return classic_target(adapter, limits=limits)


def NativeItem[FallbackT](
    factory: Callable[[], discord.ui.Item], *, fallback: PrimitiveNode[FallbackT]
) -> Extension[ComponentsV2Target | FallbackT]:
    """Create a measured Discord item with a required portable fallback."""
    return Extension(kind="discord.item", version=1, payload=factory, fallback=fallback)


DISCORD_V2_DPY27 = v2()
"""The default target: Components V2 over discord.py 2.7."""

DISCORD_V1_DPY27 = classic()
"""A classic Discord message over discord.py 2.7."""
