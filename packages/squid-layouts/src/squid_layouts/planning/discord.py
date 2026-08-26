"""Dependency-neutral Discord protocol target construction."""

from typing import Any

from squid_layouts import scene
from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.classic import CLASSIC_DIALECT
from squid_layouts.planning.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_layouts.planning.target import Target
from squid_layouts.planning.v2 import V2_DIALECT
from squid_layouts.target_types import ClassicTarget, ComponentsV2Target, DiscordAdapter

V2_TARGET_ID = V2_DIALECT.id
CLASSIC_TARGET_ID = CLASSIC_DIALECT.id

V2_PROTOCOL_CAPABILITIES = V2_DIALECT.capabilities
CLASSIC_PROTOCOL_CAPABILITIES = CLASSIC_DIALECT.capabilities


def components_v2_target[AdapterT: DiscordAdapter](
    adapter: AdapterProfile[AdapterT],
    *,
    limits: V2Limits = LIMITS,
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, AdapterT]:
    """Build a Components V2 protocol target from an adapter's verified behavior."""
    return Target(dialect=V2_DIALECT, adapter=adapter, limits=limits)


def classic_target[AdapterT: DiscordAdapter](
    adapter: AdapterProfile[AdapterT],
    *,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, AdapterT]:
    """Build a classic-message protocol target from an adapter's verified behavior."""
    return Target(dialect=CLASSIC_DIALECT, adapter=adapter, limits=limits)


def dynamic_components_v2_target(
    adapter: AdapterProfile[DiscordAdapter], *, limits: V2Limits = LIMITS
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, Any]:
    """Explicit gradual-typing escape hatch for runtime-selected adapters."""
    return components_v2_target(adapter, limits=limits)


def dynamic_classic_target(
    adapter: AdapterProfile[DiscordAdapter], *, limits: ClassicLimits = CLASSIC_LIMITS
) -> Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, Any]:
    """Explicit gradual-typing escape hatch for runtime-selected adapters."""
    return classic_target(adapter, limits=limits)
