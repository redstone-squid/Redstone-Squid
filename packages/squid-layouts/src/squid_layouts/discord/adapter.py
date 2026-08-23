"""The verified discord.py adapter profile and boundary checks."""

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

import discord
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from squid_layouts.discord.inspection import cost
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.adapter import (
    ADAPTER_DISPATCH,
    ADAPTER_INTERACTION_DELIVERY,
    ADAPTER_MODAL_FORMS,
    ADAPTER_RENDER_CLASSIC,
    ADAPTER_RENDER_V2,
    AdapterProfile,
    ExtensionAdapter,
    PreparedExtension,
)
from squid_layouts.target_types import DiscordPy27Adapter, DiscordPyAdapter
from squid_layouts.planning.target import TargetProfile


class _DiscordItemExtension:
    def prepare(self, payload: object) -> PreparedExtension:
        if not callable(payload):
            message = "discord.item extension payload must be a zero-argument factory"
            raise LayoutInvariantError(message)
        try:
            item = payload()
        except Exception as error:
            message = "discord.item factory failed during target planning"
            raise LayoutInvariantError(message) from error
        if not isinstance(item, discord.ui.Item):
            message = "discord.item factory did not return a discord.ui.Item"
            raise LayoutInvariantError(message)
        return PreparedExtension(cost(item), {"native_kind": type(item).__name__}, item)


DISCORD_PY_BEHAVIOR_CAPABILITIES = frozenset(
    {
        ADAPTER_RENDER_V2,
        ADAPTER_RENDER_CLASSIC,
        ADAPTER_DISPATCH,
        ADAPTER_INTERACTION_DELIVERY,
        ADAPTER_MODAL_FORMS,
    }
)

DISCORD_PY_27_ADAPTER = AdapterProfile(
    DiscordPy27Adapter,
    "discord.py",
    ">=2.7,<2.8",
    DISCORD_PY_BEHAVIOR_CAPABILITIES,
    {"discord.item": _DiscordItemExtension()},
)


def discord_py_adapter_profile(
    name: str,
    version_expression: str,
    *,
    capabilities: frozenset[str] = DISCORD_PY_BEHAVIOR_CAPABILITIES,
    extensions: Mapping[str, ExtensionAdapter] | None = None,
) -> AdapterProfile[DiscordPyAdapter]:
    """Declare an application-verified discord.py adapter profile."""
    return AdapterProfile(
        DiscordPyAdapter,
        name,
        version_expression,
        capabilities,
        {} if extensions is None else extensions,
    )


def require_discord_py_capability(
    profile: AdapterProfile[DiscordPyAdapter], capability: str, operation: str
) -> None:
    """Verify the selected profile and installed discord.py at an adapter boundary."""
    if capability not in profile.capabilities:
        message = f"adapter profile {profile.name!r} cannot {operation}; it lacks {capability!r}"
        raise LayoutInvariantError(message)
    try:
        installed = Version(version("discord.py"))
        applicable = SpecifierSet(profile.version_expression)
    except (PackageNotFoundError, InvalidVersion, InvalidSpecifier) as error:
        message = f"cannot verify discord.py for adapter profile {profile.name!r}: {error}"
        raise LayoutInvariantError(message) from error
    if installed not in applicable:
        message = (
            f"adapter profile {profile.name!r} applies to discord.py {profile.version_expression}, "
            f"but {installed} is installed; supply a custom profile verified for this version"
        )
        raise LayoutInvariantError(message)


def require_discord_py_target(
    target: TargetProfile[Any, Any, Any], capability: str, operation: str
) -> AdapterProfile[DiscordPyAdapter]:
    """Extract and verify the discord.py profile bound to a target."""
    profile = target.adapter
    if profile is None or not issubclass(profile.family, DiscordPyAdapter):
        message = f"target {target.id!r} cannot {operation}; it is not bound to a discord.py adapter profile"
        raise LayoutInvariantError(message)
    narrowed = cast(AdapterProfile[DiscordPyAdapter], profile)
    require_discord_py_capability(narrowed, capability, operation)
    return narrowed
