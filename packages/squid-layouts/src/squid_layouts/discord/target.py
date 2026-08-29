"""Discord target conveniences bound to the shipped discord.py adapter."""

from collections.abc import Callable
from typing import Any, overload

import discord

from squid_layouts.discord.adapter import DISCORD_PY_27_ADAPTER
from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.discord import (
    CLASSIC_PROTOCOL_CAPABILITIES,
    V2_PROTOCOL_CAPABILITIES,
    classic_target,
    components_v2_target,
)
from squid_layouts.planning.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_layouts.planning.target import TargetProfile
from squid_layouts.primitives.nodes import Extension, PrimitiveNode
from squid_layouts.scene.model import SceneClassicMessage, SceneComponentsV2
from squid_layouts.target_types import (
    ClassicTarget,
    ComponentsV2Target,
    DiscordPy27Adapter,
    DiscordPyAdapter,
)

V2_CAPABILITIES = DISCORD_PY_27_ADAPTER.combine_capabilities(V2_PROTOCOL_CAPABILITIES)
CLASSIC_CAPABILITIES = CLASSIC_PROTOCOL_CAPABILITIES


class Target[ModeT = Any, AdapterT = Any, BodyT = Any](TargetProfile[ModeT, AdapterT, BodyT]):
    """A Discord protocol mode paired with the adapter that realizes it."""

    @overload
    @classmethod
    def v2(cls, *, limits: V2Limits = LIMITS) -> Target[ComponentsV2Target, DiscordPy27Adapter, SceneComponentsV2]: ...

    @overload
    @classmethod
    def v2[ProfileT: DiscordPyAdapter](
        cls, *, adapter: AdapterProfile[ProfileT], limits: V2Limits = LIMITS
    ) -> Target[ComponentsV2Target, ProfileT, SceneComponentsV2]: ...

    @classmethod
    def v2(
        cls,
        *,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
        limits: V2Limits = LIMITS,
    ) -> Target:
        return cls._from(components_v2_target(adapter, limits=limits))

    @overload
    @classmethod
    def classic(
        cls, *, limits: ClassicLimits = CLASSIC_LIMITS
    ) -> Target[ClassicTarget, DiscordPy27Adapter, SceneClassicMessage]: ...

    @overload
    @classmethod
    def classic[ProfileT: DiscordPyAdapter](
        cls, *, adapter: AdapterProfile[ProfileT], limits: ClassicLimits = CLASSIC_LIMITS
    ) -> Target[ClassicTarget, ProfileT, SceneClassicMessage]: ...

    @classmethod
    def classic(
        cls,
        *,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
        limits: ClassicLimits = CLASSIC_LIMITS,
    ) -> Target:
        return cls._from(classic_target(adapter, limits=limits))

    @classmethod
    def _from(cls, target: TargetProfile) -> Target:
        return cls(
            id=target.id,
            version=target.version,
            capabilities=target.capabilities,
            limits=target.limits,
            extensions=target.extensions,
            dialect=target.dialect,
            resources=target.resources,
            mode=target.mode,
            adapter=target.adapter,
            body_type=target.body_type,
            selected_adapter_capabilities=target.selected_adapter_capabilities,
        )


def NativeItem[FallbackT](
    factory: Callable[[], discord.ui.Item], *, fallback: PrimitiveNode[FallbackT]
) -> Extension[ComponentsV2Target | FallbackT]:
    """Create a measured Discord item with a required portable fallback."""
    return Extension(kind="discord.item", version=1, payload=factory, fallback=fallback)


V2_TARGET = Target.v2()
CLASSIC_TARGET = Target.classic()
