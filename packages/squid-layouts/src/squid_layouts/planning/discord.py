"""Dependency-neutral Discord protocol target construction."""

from typing import Any

from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.classic import CLASSIC_DIALECT
from squid_layouts.planning.limits import CLASSIC_LIMITS, LIMITS, ClassicLimits, V2Limits
from squid_layouts.planning.target import TargetProfile
from squid_layouts.planning.v2 import V2_DIALECT
from squid_layouts.scene.model import SceneClassicMessage, SceneComponentsV2
from squid_layouts.target_types import ClassicTarget, ComponentsV2Target, DiscordAdapter

V2_PROTOCOL_CAPABILITIES = frozenset(
    {
        "actions.buttons",
        "actions.discord.premium",
        "actions.select",
        "actions.discord.entity",
        "forms.discord.entity",
        "forms.discord.file",
        "forms.discord.checkbox_group",
        "forms.modal",
        "layout.container",
        "layout.gallery",
        "layout.section",
    }
)

CLASSIC_PROTOCOL_CAPABILITIES = frozenset(
    {
        "actions.buttons",
        "actions.discord.premium",
        "actions.select",
        "actions.discord.entity",
        "forms.modal",
        "forms.discord.checkbox_group",
        "layout.embed",
        "layout.embed_fields",
        "message.content",
    }
)


def components_v2_target[AdapterT: DiscordAdapter](
    adapter: AdapterProfile[AdapterT],
    *,
    limits: V2Limits = LIMITS,
) -> TargetProfile[ComponentsV2Target, AdapterT, SceneComponentsV2]:
    """Build a Components V2 protocol target from an adapter's verified behavior."""
    extensions = adapter.extensions
    extension_capabilities = frozenset(f"extension.{kind}" for kind in extensions)
    adapter_capabilities = adapter.capabilities | extension_capabilities
    return TargetProfile(
        id="discord.components-v2",
        version=1,
        capabilities=V2_PROTOCOL_CAPABILITIES | adapter.capabilities | extension_capabilities,
        limits=limits,
        extensions=extensions,
        dialect=V2_DIALECT,
        mode=ComponentsV2Target,
        adapter=adapter,
        body_type=SceneComponentsV2,
        selected_adapter_capabilities=adapter_capabilities,
    )


def classic_target[AdapterT: DiscordAdapter](
    adapter: AdapterProfile[AdapterT],
    *,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> TargetProfile[ClassicTarget, AdapterT, SceneClassicMessage]:
    """Build a classic-message protocol target from an adapter's verified behavior."""
    return TargetProfile(
        id="discord.components-v1",
        version=1,
        capabilities=CLASSIC_PROTOCOL_CAPABILITIES | adapter.capabilities,
        limits=limits,
        dialect=CLASSIC_DIALECT,
        mode=ClassicTarget,
        adapter=adapter,
        body_type=SceneClassicMessage,
        selected_adapter_capabilities=adapter.capabilities,
    )


def dynamic_components_v2_target(
    adapter: AdapterProfile[DiscordAdapter], *, limits: V2Limits = LIMITS
) -> TargetProfile[ComponentsV2Target, Any, SceneComponentsV2]:
    """Explicit gradual-typing escape hatch for runtime-selected adapters."""
    return components_v2_target(adapter, limits=limits)


def dynamic_classic_target(
    adapter: AdapterProfile[DiscordAdapter], *, limits: ClassicLimits = CLASSIC_LIMITS
) -> TargetProfile[ClassicTarget, Any, SceneClassicMessage]:
    """Explicit gradual-typing escape hatch for runtime-selected adapters."""
    return classic_target(adapter, limits=limits)
