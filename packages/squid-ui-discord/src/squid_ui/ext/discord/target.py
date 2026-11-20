"""Discord target conveniences bound to the shipped discord.py adapter."""

from typing import cast

from squid_ui.ext.discord import scene
from squid_ui.ext.discord.adapter import DISCORD_ITEM, DISCORD_PY_27_ADAPTER, ItemFactory
from squid_ui.ext.discord.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_ui.ext.discord.planning.discord import (
    CLASSIC_PROTOCOL_CAPABILITIES,
    V2_PROTOCOL_CAPABILITIES,
    classic_target,
    components_v2_target,
)
from squid_ui.ext.discord.primitives.nodes import Extension, Node
from squid_ui.ext.discord.target_types import ClassicTarget, ComponentsV2Target, DiscordPy27Adapter, DiscordPyAdapter
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.target_types import Renderable

V2_CAPABILITIES = DISCORD_PY_27_ADAPTER.combine_capabilities(V2_PROTOCOL_CAPABILITIES)
CLASSIC_CAPABILITIES = CLASSIC_PROTOCOL_CAPABILITIES


def v2[ProfileT: DiscordPyAdapter = DiscordPy27Adapter](
    *, adapter: AdapterProfile[ProfileT] = DISCORD_PY_27_ADAPTER, limits: V2Limits = LIMITS
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, ProfileT]:
    """A Components V2 target realized by discord.py."""
    return components_v2_target(adapter, limits=limits)


def classic[ProfileT: DiscordPyAdapter = DiscordPy27Adapter](
    *, adapter: AdapterProfile[ProfileT] = DISCORD_PY_27_ADAPTER, limits: ClassicLimits = CLASSIC_LIMITS
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, ProfileT]:
    """A classic-message target realized by discord.py."""
    return classic_target(adapter, limits=limits)


def NativeItem[FallbackT](
    factory: ItemFactory, *, fallback: Renderable[FallbackT]
) -> Extension[ItemFactory, ComponentsV2Target | FallbackT]:
    """Create a measured Discord item with a required portable fallback."""
    return cast(
        Extension[ItemFactory, ComponentsV2Target | FallbackT],
        Extension(kind=DISCORD_ITEM, version=1, payload=factory, fallback=cast(Node, fallback)),
    )


DISCORD_V2_DPY27 = v2()
"""The default target: Components V2 over discord.py 2.7."""

DISCORD_V1_DPY27 = classic()
"""A classic Discord message over discord.py 2.7."""
